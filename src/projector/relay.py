"""The relay: agents' presence and messages carried between machines through hidden refs in a repository that
both machines work in.

The relay runs on a machine that holds `agent.key`, an X25519 private key in the OS keychain or inline in
`~/.projector.toml`. Every record is sealed with `crypto.seal` to the key's own public key before it leaves the
machine, so the repository holds only ciphertext, under ref names made of the key's ID and random IDs, and only
machines holding the key can read or write the records.

Commands never push and never write a Git object: they queue each ref change in the repository's outbox. The watch
daemon runs one job per repository on a pool of worker threads, never on the loop that delivers events. A job
fetches the key's refs, opens and checks what is new, delivers messages to the sessions on this machine, queues
renewals and cleanup, then writes each queued record as a commit in a checkout of the repository and pushes every
queued change in one `git push`.

Files under the watch state root:

    key.json                       where `agent.key`'s private key is stored, and why when it is inline
    outbox/<owner>/<repo>.json     the ref changes waiting for a push, and the checkouts that queued them
    relay/<owner>/<repo>.json      the repository's visibility, the rejected count and the last 20 rejections, the
                                   key's refs on origin, what each held when it was opened, and other machines'
                                   agents

Refs in the repository, each a parentless commit whose tree holds one `sealed.json`:

    refs/projector/agents/<key-id>/<relay-id>          a session's presence, with when its lease expires
    refs/projector/inbox/<key-id>/<relay-id>/<id>      a message to the session with that relay ID
    refs/projector/user/<key-id>/<id>                  an entry in the repository inbox of the person whose key it
                                                       is, sealed by their key or a peer's (`repo_inbox.py`)
    refs/projector/user-state/<key-id>/<id>            the state of that person's entry, sealed by their own key

The agents and inbox namespaces open only records this machine's own key sealed. The repository inbox also opens
a peer's, whose public key `agent.peers` lists.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Callable, Optional

from . import agents, config, crypto, keychain, repo_inbox, review, summary, watch
from .config import INLINE_KEY, KEYCHAIN, ConfigError, inline_key, unset_value, user_config_path, write_value
from .core import ProjectorError, run_bounded
from .inbox import SESSION, locked, read_json, write_json
from .keychain import KeychainError

REMOTE = "origin"
AGENTS = "refs/projector/agents/"
INBOX = "refs/projector/inbox/"
USER = "refs/projector/user/"
USER_STATE = "refs/projector/user-state/"
KEY_ID = re.compile(r"^[0-9a-f]{16}$")
PRESENCE_REF = re.compile(r"^refs/projector/agents/([0-9a-f]{16})/([0-9a-f]{16})$")
INBOX_REF = re.compile(r"^refs/projector/inbox/([0-9a-f]{16})/([0-9a-f]{16})/(m-[0-9a-f]{32})$")
USER_REF = re.compile(r"^refs/projector/user/([0-9a-f]{16})/(u-[0-9a-f]{32})$")
STATE_REF = re.compile(r"^refs/projector/user-state/([0-9a-f]{16})/(u-[0-9a-f]{32})$")
TREE = re.compile(r"^100644 blob ([0-9a-f]{40,64}) +([0-9]+)\tsealed\.json$")
LEASE = 3600.0
RENEW = 1800.0
MAX_SEALED = 32 * 1024
# A sealed record and the record inside it are flat objects, so JSON nested deeper was planted and is not parsed.
MAX_DEPTH = 8
QUOTE, BACKSLASH = ord('"'), ord("\\")
OPENS, CLOSES = frozenset(b"[{"), frozenset(b"]}")
OPEN_LIMIT = 64
EXAMINE_LIMIT = 4 * OPEN_LIMIT
FETCH_TIMEOUT = 120.0
PUSH_TIMEOUT = 900.0
WORKERS = 2
COMMIT_MESSAGE = "projector relay"
IDENTITY = {"GIT_AUTHOR_NAME": "projector", "GIT_AUTHOR_EMAIL": "projector@invalid", "GIT_AUTHOR_DATE": "@0 +0000",
            "GIT_COMMITTER_NAME": "projector", "GIT_COMMITTER_EMAIL": "projector@invalid",
            "GIT_COMMITTER_DATE": "@0 +0000"}
STALE = ("stale info", "fetch first", "non-fast-forward")
NO_KEY = "no agent.key is set; run `project agent key init` to make one"
NO_ORIGIN = "this checkout has no GitHub origin to relay through"
NO_CHECKOUT = "no recorded checkout of the repository remains"
PUBLIC_REMINDER = "public repository: name no private repository and describe none of its internals"
COPY = "To give another machine this key, run `project agent key join` there in a terminal."
SHARE = "Give this public key to anyone whose agents should reach your inbox; it is not secret."
REQUEST_PREFIX = "pj1-"
LINE_PREFIX = "pk1-"
REQUEST = re.compile(r"pj1-([a-z0-9-]{0,20})-([A-Za-z0-9_-]{43})")
# `pk1-` and the unpadded base64url of a sealed record of a 32-byte plaintext, whose JSON is 235 bytes.
LINE_LENGTH = 318
LABEL_LENGTH = 20
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# Each reason the relay rejects a record, by the code `watch status --json` reports, with the words its text prints.
REASONS = {
    "ref-name": "ref name", "tree-shape": "tree shape", "too-large": "too large", "unreadable": "unreadable blob",
    "too-deep": "JSON too deep", "not-json": "not JSON", "not-sealed": "not a sealed record",
    "tag-mismatch": "tag mismatch", "other-key": "sealed by another key", "relay-id-mismatch": "relay ID mismatch",
    "recipient-mismatch": "recipient mismatch", "message-id-mismatch": "message ID mismatch", "expired": "expired",
    "field-shape": "field shape", "collision": "ID collision", "peer-limit": "over agent.peer_limit",
    "sender-mismatch": "sender mismatch", "no-sent-copy": "no sent copy", "location-mismatch": "location mismatch",
    "unexpected": "unexpected error",
}
# The errors a record's contents could raise in code its shape checks should have guarded, which reject that record
# alone, as `unexpected`, so one record the checks missed cannot fail every later pass. A `ProjectorError`, such as a
# Git command that timed out or a lock another command holds, is not the record's doing and still ends the job.
RECORD_ERRORS = (ArithmeticError, AttributeError, LookupError, TypeError, ValueError)
KEEP_REJECTIONS = 20
KEEP_SENDERS = 20


class RelayError(ProjectorError):
    """A relay key or a Git command that the relay cannot use."""


class Rejected(ValueError):
    """A record the relay refuses, and the code in `REASONS` that says why."""

    def __init__(self, reason: str, sender: Optional[bytes] = None) -> None:
        super().__init__(REASONS[reason])
        self.reason = reason
        # The public key that sealed an `other-key` record, which its tag proved.
        self.sender = sender


# The key

class Key:
    """The relay's key pair. Its repr names only the key's ID and store, so no log or traceback shows it."""

    def __init__(self, private: bytes, store: str) -> None:
        self.private = private
        self.store = store
        self.public = crypto.public_key(private)
        self.id = crypto.key_id(self.public)

    def __repr__(self) -> str:
        return f"Key({self.id}, {self.store})"


def configured(environ: Optional[dict] = None) -> Optional[str]:
    """`agent.key` from `~/.projector.toml` alone."""
    return config.user_value("agent.key", environ)


def value_id(value: str) -> str:
    """The key ID an `agent.key` value names, read without the keychain."""
    return config.key_id_of(value)


def read_key(value: str, timeout: Optional[float] = None) -> Key:
    """The key an `agent.key` value holds or names, reading the keychain within `timeout` seconds."""
    if not value.startswith(KEYCHAIN):
        return Key(inline_key(value), "inline")
    wanted = value[len(KEYCHAIN):]
    if not KEY_ID.fullmatch(wanted):
        raise RelayError("agent.key names no key ID after keychain:; it is keychain: and 16 hex digits")
    key = Key(inline_key(INLINE_KEY + keychain.read(wanted, timeout)), "keychain")
    if key.id != wanted:
        raise RelayError(f"the keychain item for key {wanted} holds key {key.id}")
    return key


def command_key(environ: Optional[dict] = None) -> tuple[Optional[Key], str]:
    """The key for a command that seals, read once, or None and why the relay is off."""
    try:
        value = configured(environ)
        return (read_key(value), "") if value else (None, NO_KEY)
    except (ConfigError, KeychainError, RelayError) as error:
        return None, str(error)


def config_lock(environ: Optional[dict] = None) -> AbstractContextManager[None]:
    """The lock that every change to `~/.projector.toml` holds from its read of the file to its write. A change
    reads the whole file and writes it back, so without the lock, two that overlap write back a copy that drops
    the other's change, which can trust a peer just removed again."""
    return locked(watch.state_root(environ) / "user-config")


def no_keychain(reason: str) -> RelayError:
    return RelayError(f"{reason}, so no keychain can hold the key and nothing was stored; pass --inline to store "
                      "it in ~/.projector.toml instead, where every process running as you can read it")


def save_key(root: Path, private: bytes, *, inline: bool, environ: Optional[dict] = None) -> tuple[Key, str, str]:
    """Store the private key in the keychain, or inline with `inline`, and write `agent.key` to
    `~/.projector.toml` with mode 0600. Answers the key, where it is stored, and why when that is inline.
    Without `inline`, a keychain that cannot store the key stores nothing."""
    key = Key(private, "inline" if inline else "keychain")
    key_id = key.id
    secret = crypto.encode(private)
    value, store, reason = INLINE_KEY + secret, "inline", "you passed --inline"
    if not inline:
        try:
            store = keychain.store(key_id, secret)
        except KeychainError as error:
            raise no_keychain(str(error)) from None
        value, reason = KEYCHAIN + key_id, ""
    path = user_config_path(environ)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    if os.name == "posix":
        os.chmod(path, 0o600)
    with config_lock(environ):
        write_value(path, "agent.key", value)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "key.json", {"schema_version": 1, "key_id": key_id, "store": store, "reason": reason})
    return key, store, reason


def init_key(root: Path, *, inline: bool, environ: Optional[dict] = None) -> tuple[Key, str, str]:
    existing = configured(environ)
    if existing:
        raise RelayError(f"agent.key already holds key {value_id(existing)}; run `project agent key remove` "
                         "first to make a new one, which no message sealed to the old key can be opened with")
    return save_key(root, crypto.new_private_key(), inline=inline, environ=environ)


def unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def from_unpadded(text: str) -> bytes:
    """The bytes of unpadded base64url `text`, which must be the one encoding of them."""
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise RelayError("it is not base64url") from None
    if unpadded(data) != text:
        raise RelayError("it is not base64url")
    return data


def machine_label(hostname: str) -> str:
    """The short host name, as lowercase letters, digits, and hyphens, cut to 20 characters."""
    short = re.sub(r"[^a-z0-9-]", "", hostname.split(".")[0].lower())[:LABEL_LENGTH]
    return short or "machine"


def fingerprint(public: bytes) -> str:
    """Three groups of three Crockford base32 characters: the first 45 bits of the SHA-256 of `public`."""
    bits = int.from_bytes(hashlib.sha256(public).digest()[:6], "big") >> 3
    text = "".join(CROCKFORD[(bits >> shift) & 31] for shift in range(40, -1, -5))
    return f"{text[:3]}-{text[3:6]}-{text[6:]}"


def check_prefix(text: str, prefix: str, kind: str, maker: str) -> None:
    if text.startswith(prefix):
        return
    stem = prefix[:2]
    version = re.match(re.escape(stem) + r"([0-9]+)-", text)
    if version:
        raise RelayError(f"the {kind} is version {version.group(1)}, and this Projector reads only version 1, "
                         f"which starts with {prefix}")
    raise RelayError(f"that is not a {kind} that {maker} prints; it starts with {prefix}")


def make_request(private: bytes, hostname: str) -> str:
    """The request `project agent key join` prints: pj1-, the machine label, and the temporary public key."""
    return f"{REQUEST_PREFIX}{machine_label(hostname)}-{unpadded(crypto.public_key(private))}"


def parse_request(request: str) -> tuple[str, bytes]:
    """A request's machine label and the temporary public key it carries."""
    text = request.strip()
    check_prefix(text, REQUEST_PREFIX, "request", "`project agent key join`")
    found = REQUEST.fullmatch(text)
    if found is None:
        raise RelayError(f"the request is {len(text)} characters; a request is pj1-, a machine label of up to "
                         f"{LABEL_LENGTH} lowercase letters, digits, and hyphens, a hyphen, and the 43 characters of "
                         "a public key")
    try:
        public = from_unpadded(found.group(2))
    except RelayError as error:
        raise RelayError(f"the request's public key is not usable: {error}") from None
    return found.group(1), public


def grant_line(key: Key, recipient: bytes) -> str:
    """`key`'s private bytes sealed by `key` to the public key `recipient`, as the line
    `project agent key join` reads."""
    sealed = crypto.seal(key.private, recipient, key.private)
    return LINE_PREFIX + unpadded(json.dumps(sealed, separators=(",", ":")).encode())


def accept_line(private: bytes, line: str) -> bytes:
    """The relay key a line from `project agent key grant` carries, opened with the temporary `private` key.
    The line must have been sealed by the key it carries."""
    text = line.strip()
    check_prefix(text, LINE_PREFIX, "line", "`project agent key grant`")
    if len(text) != LINE_LENGTH:
        raise RelayError(f"the line is {len(text)} characters, and a line `project agent key grant` prints is "
                         f"{LINE_LENGTH}")
    try:
        sealed = json.loads(from_unpadded(text[len(LINE_PREFIX):]))
    except (RelayError, ValueError):
        raise RelayError("the line is not an encrypted key; copy it again from `project agent key grant`") from None
    try:
        sender, carried = crypto.unseal(private, sealed)
    except crypto.CryptoError as error:
        raise RelayError(f"the line does not open with this request's key: {error}; it was encrypted to another "
                         "request, or it changed on the way") from None
    if len(carried) != 32 or crypto.public_key(carried) != sender:
        raise RelayError("the line carries a key other than the key that encrypted it, so it is not one "
                         "`project agent key grant` printed")
    return carried


def replaceable(replace: bool, environ: Optional[dict] = None) -> Optional[str]:
    """The `agent.key` that `join` replaces, refused without `replace`."""
    existing = configured(environ)
    if existing and not replace:
        raise RelayError(f"agent.key already holds key {value_id(existing)}; pass --replace to replace it with the "
                         "key you join, after which no message sealed to it can be opened")
    return existing


def check_join(*, inline: bool, replace: bool, environ: Optional[dict] = None) -> None:
    """Refuse `project agent key join` before it makes a request when it could not store the key."""
    replaceable(replace, environ)
    if not inline:
        missing = keychain.usable()
        if missing:
            raise no_keychain(missing)


def join_key(root: Path, private: bytes, *, inline: bool, replace: bool,
             environ: Optional[dict] = None) -> tuple[Key, str, str]:
    """Store the key `join` accepted, and then delete the keychain item of a different key it replaced."""
    existing = replaceable(replace, environ)
    saved = save_key(root, private, inline=inline, environ=environ)
    if existing and existing.startswith(KEYCHAIN) and value_id(existing) != saved[0].id:
        try:
            keychain.delete(value_id(existing))
        except KeychainError:
            pass
    return saved


def show_key(environ: Optional[dict] = None) -> dict:
    value = configured(environ)
    if not value:
        raise RelayError(NO_KEY)
    key = read_key(value)
    return {"key_id": key.id, "public_key": crypto.encode(key.public), "store": key.store}


# Peers

def check_peer(name: str, text: str, *, replace: bool, environ: Optional[dict] = None) -> tuple[str, bytes]:
    """The peer `project agent peer add` would write, by its lowercased name, with its public key: refused for a
    name that is not a peer name or that names a peer already, unless `replace`, and for a key that is not one,
    that is your own, or that another name holds."""
    name = name.lower()
    if not agents.NAME.fullmatch(name):
        raise RelayError(f"{name!r} is not a peer name: use 1 to 32 letters, digits, and hyphens, starting with a "
                         "letter or digit")
    try:
        public = config.public_key(text)
    except ConfigError as error:
        raise RelayError(str(error)) from None
    key_id = crypto.key_id(public)
    found = config.peers(environ)
    if name in found and not replace:
        raise RelayError(f"{name} is already a peer, with key {crypto.key_id(found[name])}; pass --replace to give "
                         f"{name} this key instead")
    value = configured(environ)
    if value and value_id(value) == key_id:
        raise RelayError(f"key {key_id} is your own key, which needs no peer entry")
    for other, held in found.items():
        if other != name and held == public:
            raise RelayError(f"{other} already holds key {key_id}; run `project agent peer remove {other}` first to "
                             f"list it as {name}")
    return name, public


def add_peer(name: str, public: bytes, *, replace: bool = False, environ: Optional[dict] = None) -> None:
    """Write the peer to `~/.projector.toml`, checking it again against the table as it is now, under
    `config_lock`, since another `peer add` may have changed the table while this one waited for its
    confirmation."""
    with config_lock(environ):
        check_peer(name, crypto.encode(public), replace=replace, environ=environ)
        path = user_config_path(environ)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        write_value(path, f"agent.peers.{name}", crypto.encode(public))


def remove_peer(name: str, environ: Optional[dict] = None) -> str:
    """Remove a peer from `~/.projector.toml`, under `config_lock`, and answer the ID of the key it held."""
    name = name.lower()
    with config_lock(environ):
        found = config.peers(environ)
        if name not in found:
            listed = ", ".join(sorted(found)) or "none"
            raise RelayError(f"{name} is not a peer; your peers: {listed}")
        unset_value(user_config_path(environ), f"agent.peers.{name}")
    return crypto.key_id(found[name])


def peer_rows(root: Path, environ: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """Each peer, by name, with its key's ID and public key and when an entry from them last opened here, and
    each key that is no peer of yours whose records the relay rejected, newest first."""
    found = config.peers(environ)
    last = repo_inbox.read_counts(root)["last_opened"]
    rows = [{"name": name, "key_id": crypto.key_id(public), "public_key": crypto.encode(public),
             "last_opened": last.get(crypto.key_id(public))} for name, public in sorted(found.items())]
    known = {row["key_id"] for row in rows}
    unknown: dict[str, dict] = {}
    for path in (root / "relay").glob("*/*.json"):
        cache = read_json(path) or {}
        for key_id, sender in (cache.get("senders") or {}).items():
            if key_id not in known and (key_id not in unknown or sender["at"] > unknown[key_id]["at"]):
                unknown[key_id] = {"key_id": key_id, "public_key": sender["public_key"], "at": sender["at"],
                                   "repo": cache.get("repo")}
    return rows, sorted(unknown.values(), key=lambda sender: sender["at"], reverse=True)


def remove_key(root: Path, environ: Optional[dict] = None) -> tuple[str, str]:
    """Delete `agent.key` and the keychain item it names, and answer the key's ID and what removing the item
    found."""
    value = configured(environ)
    if not value:
        raise RelayError("no agent.key is set, so there is no key to remove")
    removed = value_id(value)
    found = "the key was stored inline"
    if value.startswith(KEYCHAIN):
        try:
            keychain.delete(removed)
            found = "the keychain item is deleted"
        except KeychainError as error:
            found = f"the keychain item could not be deleted: {error}"
    with config_lock(environ):
        unset_value(user_config_path(environ), "agent.key")
    (root / "key.json").unlink(missing_ok=True)
    return removed, found


def inline_reason(root: Path, value: str) -> str:
    stored = read_json(root / "key.json") or {}
    if stored.get("key_id") == value_id(value) and stored.get("store") == "inline":
        return stored.get("reason") or "set inline"
    return "set inline in ~/.projector.toml"


def header(root: Path, repo: Optional[str], environ: Optional[dict] = None) -> tuple[bool, str]:
    """Whether the relay runs for a session in `repo`, and the line `project agent list` prints about it. Reads
    no keychain: a keychain error comes from the daemon's last read."""
    try:
        value = configured(environ)
    except ConfigError as error:
        return False, f"local only: {error}"
    if not value:
        return False, f"local only: {NO_KEY}"
    if not repo:
        return False, f"local only: {NO_ORIGIN}"
    reported = ((read_json(root / "status.json") or {}).get("relay") or {}).get("key") or {}
    if reported.get("error") and reported.get("id") == value_id(value):
        return False, f"local only: the key could not be read: {reported['error']}"
    if value.startswith(KEYCHAIN):
        return True, f"relay: on, key {value_id(value)} in the keychain"
    return True, f"relay: on, key {value_id(value)} stored inline: {inline_reason(root, value)}"


# Refs and the files that describe them

def presence_ref(key_id: str, relay_id: str) -> str:
    return f"{AGENTS}{key_id}/{relay_id}"


def inbox_ref(key_id: str, relay_id: str, message_id: str) -> str:
    return f"{INBOX}{key_id}/{relay_id}/{message_id}"


def user_ref(key_id: str, entry_id: str) -> str:
    return f"{USER}{key_id}/{entry_id}"


def state_ref(key_id: str, entry_id: str) -> str:
    return f"{USER_STATE}{key_id}/{entry_id}"


def parse_ref(ref: str) -> Optional[tuple[str, str, str]]:
    """A relay ref's kind, its key ID, and what it names: an `agent`'s presence or a `message` to it, by the
    agent's relay ID, or a repository inbox entry, `user`, or its `state`, by the entry's ID."""
    for kind, pattern in (("agent", PRESENCE_REF), ("message", INBOX_REF), ("user", USER_REF), ("state", STATE_REF)):
        if found := pattern.fullmatch(ref):
            return kind, found[1], found[2]
    return None


def tracking(ref: str) -> str:
    return summary.tracking_ref(REMOTE, ref)


def repo_file(root: Path, folder: str, repo: str) -> Path:
    owner, name = repo.lower().split("/")
    return root / folder / owner / f"{name}.json"


def read_cache(root: Path, repo: str) -> dict:
    found = read_json(repo_file(root, "relay", repo)) or {}
    return {"schema_version": 1, "repo": found.get("repo") or repo, "polled_at": found.get("polled_at"),
            "visibility": found.get("visibility") or "unknown", "rejected": found.get("rejected") or 0,
            "rejections": found.get("rejections") or [], "senders": found.get("senders") or {},
            "agents": found.get("agents") or {}, "remote": found.get("remote") or {}, "seen": found.get("seen") or {}}


def visibility(root: Path, repo: str) -> str:
    return read_cache(root, repo)["visibility"]


def remote_agents(root: Path, now: float, repos: Optional[set[str]] = None) -> dict[str, dict]:
    """Other machines' agents whose leases run, from the relay's cache, by session ID."""
    found: dict[str, dict] = {}
    for path in sorted((root / "relay").glob("*/*.json")):
        cache = read_json(path) or {}
        repo = cache.get("repo")
        if not isinstance(repo, str) or (repos is not None and repo.lower() not in repos):
            continue
        for agent in (cache.get("agents") or {}).values():
            expires = agent.get("expires_at") if isinstance(agent, dict) else None
            if isinstance(expires, (int, float)) and expires > now and isinstance(agent.get("session"), str):
                found[agent["session"]] = {**agent, "repo": repo}
    return found


def remote_reviews(root: Path, repo: str, login: str, now: float) -> Optional[list[list[dict]]]:
    """The review targets of each live presence of `login` from another machine in the relay's cache for `repo`,
    one list for each presence, or None when the relay has not polled the repository within a lease. A presence
    from a version before review targets reviews nothing."""
    cache = read_cache(root, repo)
    polled = watch.seconds_of(cache["polled_at"])
    if polled is None or now - polled >= LEASE:
        return None
    machine = agents.this_machine(root)
    found = []
    for agent in cache["agents"].values():
        if (isinstance(agent, dict) and str(agent.get("login") or "").lower() == login.lower()
                and agent.get("machine") != machine and isinstance(agent.get("expires_at"), (int, float))
                and agent["expires_at"] > now):
            reviews = agent.get("reviews") if isinstance(agent.get("reviews"), list) else []
            found.append([entry for entry in reviews if agents.review_entry(entry, repo)])
    return found


# The outbox

def read_outbox(path: Path) -> dict:
    found = read_json(path) or {}
    return {"schema_version": 1, "checkouts": found.get("checkouts") or [], "items": found.get("items") or [],
            "pushing": found.get("pushing") or []}


def outbox_items(root: Path, repo: str) -> list[dict]:
    return read_outbox(repo_file(root, "outbox", repo))["items"]


def change(ref: str, sealed: Optional[dict]) -> tuple[str, str]:
    """A queued change as a job tries to push it: its ref and what the ref is to hold."""
    return ref, json.dumps(sealed, sort_keys=True)


def pending(box: dict) -> frozenset[tuple[str, str]]:
    return frozenset(change(item["ref"], item["sealed"]) for item in box["items"])


def queue(root: Path, repo: str, cwd: Optional[str], changes: list[tuple[str, Optional[dict], dict]],
          now: float, remote: Optional[dict[str, str]] = None) -> None:
    """Queue each `(ref, sealed, meta)` change, with `sealed` None for a deletion, keeping at most one item per ref,
    because one push refuses two updates to one ref. A change to a queued ref keeps the item's `old`, the commit
    on origin that the lease must expect. `remote` holds the refs on origin, as the cache last recorded them unless
    a running job knows better."""
    path = repo_file(root, "outbox", repo)
    remote = read_cache(root, repo)["remote"] if remote is None else remote
    with locked(path.parent):
        box = read_outbox(path)
        before = json.loads(json.dumps(box))
        items = {item["ref"]: item for item in box["items"]}
        pushing = set(box["pushing"])
        for ref, sealed, meta in changes:
            item = items.get(ref)
            if item is None:
                if sealed is None and ref not in remote:
                    continue
                items[ref] = {"ref": ref, "sealed": sealed, "meta": meta, "old": remote.get(ref), "cwd": cwd,
                              "queued_at": watch.stamp(now), "last_error": ""}
            elif sealed is None and item["old"] is None and ref not in pushing:
                del items[ref]
            else:
                item.update(sealed=sealed, meta=meta, queued_at=watch.stamp(now), cwd=cwd or item.get("cwd"))
        if cwd:
            box["checkouts"] = [{"cwd": cwd, "queued_at": watch.stamp(now)},
                                *[found for found in box["checkouts"] if found.get("cwd") != cwd]]
        box["items"] = list(items.values())
        if box != before:
            write_json(path, box)


def presence(root: Path, session: str, found: dict, now: float) -> dict:
    """The session's presence: its registration, what it reviews in the registration's repository as its
    subscriptions and this machine's adoptions say when the presence is sealed, and its lease."""
    record = {key: value for key, value in found.items() if key not in ("relay", "ended_at", "summary_auto")}
    repo = found.get("repo")
    reviews = agents.review_targets(watch.subscriptions(watch.session_folder(root, session)), repo,
                                    watch.read_adopted(root, repo)[0] if repo else ())
    return {**record, "reviews": reviews, "session": session, "expires_at": now + LEASE}


def seal_to(key: Key, recipient: bytes, record: dict) -> dict:
    """`record` sealed by `key` to the public key `recipient`."""
    return crypto.seal(key.private, recipient, json.dumps(record, ensure_ascii=False, sort_keys=True).encode())


def seal(key: Key, record: dict) -> dict:
    return seal_to(key, key.public, record)


def sealed_text(sealed: dict) -> str:
    """A sealed record as the `sealed.json` the relay commits."""
    return json.dumps(sealed, sort_keys=True)


def publish(root: Path, session: str, found: dict, key: Key, now: float) -> None:
    """Queue the session's sealed presence."""
    record = presence(root, session, found, now)
    queue(root, found["repo"], found["cwd"], [(presence_ref(key.id, found["relay_id"]), seal(key, record),
                                               presence_meta(record))], now)


def presence_meta(record: dict) -> dict:
    """What the outbox keeps beside a sealed presence: its lease, and a digest of what it says, so a push marks
    the registration published only while the registration still says that."""
    return {"kind": "agent", "expires_at": record["expires_at"], "digest": digest(record)}


def digest(record: dict) -> str:
    """A digest of a presence record apart from its lease."""
    content = {key: value for key, value in record.items() if key != "expires_at"}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def forget(root: Path, found: dict, now: float) -> int:
    """Queue the deletion of every ref under the relay ID of the registration `found` that origin holds, as the
    cache last recorded it, or that waits in the outbox. Answers how many refs on origin it queued for deletion."""
    repo, relay_id = found.get("repo"), found.get("relay_id")
    if not repo or not relay_id:
        return 0
    remote = read_cache(root, repo)["remote"]
    refs = {ref for ref in [*remote, *(item["ref"] for item in outbox_items(root, repo))]
            if (parsed := parse_ref(ref)) and parsed[2] == relay_id}
    queue(root, repo, found.get("cwd"), [(ref, None, {}) for ref in sorted(refs)], now, remote)
    return sum(1 for item in outbox_items(root, repo) if item["ref"] in refs and item["sealed"] is None)


def queue_message(root: Path, repo: str, cwd: str, key: Key, message: dict, now: float) -> None:
    meta = {"kind": "message", "to": message["to"], "sent_at": watch.seconds_of(message["sent_at"])}
    queue(root, repo, cwd, [(inbox_ref(key.id, message["to"], message["id"]), seal(key, message), meta)], now)


# Git

def git(cwd: Path, *args: str, input: Optional[bytes] = None, env: Optional[dict] = None,
        timeout: float = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    """Run Git on the repository at `cwd` with standard input closed or fed `input`, no terminal prompt, and a
    timeout after which its process group is killed. It drops the inherited `config.GIT_LOCATION` variables, such
    as the `GIT_DIR` a Git hook sets, which would point Git at another repository than `cwd`'s."""
    inherited = {name: value for name, value in os.environ.items() if name not in config.GIT_LOCATION}
    environment = {**inherited, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        return run_bounded(["git", *args], cwd=cwd, input=input, env=environment, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RelayError(f"git {args[0]} did not finish within {timeout:g} seconds, so it was stopped") from None
    except OSError as error:
        raise RelayError(f"git could not start: {error}") from None


def shallow(data: bytes) -> bool:
    """Whether the JSON in `data` nests arrays and objects at most `MAX_DEPTH` deep, counting the brackets
    outside its strings, so `json.loads` never recurses deeply into a blob that anyone who can push planted. One
    pass over the bytes, with no regular expression, so no blob makes it backtrack."""
    depth = 0
    inside = escaped = False
    for byte in data:
        if inside:
            if escaped:
                escaped = False
            elif byte == BACKSLASH:
                escaped = True
            elif byte == QUOTE:
                inside = False
        elif byte == QUOTE:
            inside = True
        elif byte in OPENS:
            depth += 1
            if depth > MAX_DEPTH:
                return False
        elif byte in CLOSES:
            depth -= 1
    return True


def parse(data: bytes) -> object:
    """The JSON an untrusted blob holds, raising `Rejected` for any larger than `MAX_SEALED`, or that does not
    parse or nests too deeply."""
    if len(data) > MAX_SEALED:
        raise Rejected("too-large")
    if not shallow(data):
        raise Rejected("too-deep")
    try:
        return json.loads(data)
    except RecursionError:
        raise Rejected("too-deep") from None
    except ValueError:
        raise Rejected("not-json") from None


def text(data: bytes) -> str:
    return data.decode("utf-8", "replace").strip()


def last_line(done: subprocess.CompletedProcess) -> str:
    lines = text(done.stderr).splitlines()
    return lines[-1] if lines else f"exited with status {done.returncode}"


def checked(cwd: Path, *args: str, **options) -> str:
    done = git(cwd, *args, **options)
    if done.returncode != 0:
        raise RelayError(f"git {args[0]} failed: {last_line(done)}")
    return text(done.stdout)


def write_commit(cwd: Path, sealed: dict) -> str:
    """A parentless commit holding `sealed.json`, with a fixed identity, date, and message and no signature, so
    nothing about the checkout or the session goes into the repository outside the sealed blob."""
    blob = checked(cwd, "hash-object", "-w", "--stdin", input=sealed_text(sealed).encode())
    tree = checked(cwd, "mktree", input=f"100644 blob {blob}\tsealed.json\n".encode())
    return checked(cwd, "commit-tree", "--no-gpg-sign", "-m", COMMIT_MESSAGE, tree, env=IDENTITY)


def push_results(stdout: str) -> dict[str, tuple[bool, str]]:
    """Each ref's outcome from `git push --porcelain`: whether it landed, and the summary Git gave."""
    found = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and len(parts[0]) == 1 and ":" in parts[1]:
            found[parts[1].split(":", 1)[1]] = (parts[0] != "!", parts[2])
    return found


def origin_is(cwd: Path, repo: str) -> bool:
    try:
        return cwd.is_dir() and review.origin_repo(cwd).lower() == repo.lower()
    except review.ReviewError:
        return False


# One repository's job

class Job:
    """One pass of the relay over one repository, run on a worker thread: poll, open, deliver, renew, clean up,
    and push."""

    def __init__(self, root: Path, repo: str, key: Optional[Key], live: set[str], now: float, *, poll: bool,
                 full: bool, gh: Callable[[list[str]], watch.GhResult], held: Optional[str] = None,
                 peers: Optional[dict[str, bytes]] = None, peer_limit: Optional[int] = None) -> None:
        self.root = root
        self.repo = repo
        self.key = key
        # Each peer's key, by its ID, with your name for them.
        self.peers = {crypto.key_id(public): (name, public) for name, public in (peers or {}).items()}
        self.peer_limit = agents.PEER_LIMIT if peer_limit is None else peer_limit
        # The key agent.key names, which a locked keychain can keep the daemon from reading.
        self.held = key.id if key else held
        found, gone = agents.relayed(root)
        # A session that unregistered is no longer live to the relay, so its refs are deleted as a lapsed one's.
        self.live = live - gone
        self.now = now
        self.poll = poll
        self.full = full
        self.gh = gh
        self.cache = read_cache(root, repo)
        self.machine = agents.this_machine(root)
        self.sessions = {name: record for name, record in found.items()
                         if (record.get("repo") or "").lower() == repo.lower()}
        self.relays = {found["relay_id"]: name for name, found in self.sessions.items()
                       if isinstance(found.get("relay_id"), str)}
        self.changes: list[tuple[str, Optional[dict], dict]] = []
        # The rejections this job made, which the daemon logs.
        self.rejections: list[dict] = []
        self.error = ""
        # The changes this job tried to push: those its push took, or, when it pushes nothing, those queued when
        # it started and those it queued. A change queued after that starts the next job without a poll.
        self.attempted: frozenset[tuple[str, str]] = frozenset()

    def run(self) -> dict:
        self.attempted = pending(read_outbox(repo_file(self.root, "outbox", self.repo)))
        cwd = self.checkout()
        try:
            if self.poll and cwd is not None and self.key_ids():
                self.fetch(cwd, self.key_ids())
                self.rebase(cwd)
                if self.key is not None:
                    self.examine(cwd)
                    if self.full:
                        self.sweep()
            if self.full:
                self.read_visibility()
            self.maintain()
            queue(self.root, self.repo, None, self.changes, self.now, self.cache["remote"])
            self.attempted |= {change(ref, sealed) for ref, sealed, _ in self.changes}
            if cwd is not None:
                self.push(cwd)
        except RelayError as error:
            self.error = str(error)
        finally:
            path = repo_file(self.root, "relay", self.repo)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, self.cache)
        if cwd is None:
            return self.result("waiting", NO_CHECKOUT)
        return self.result("error" if self.error else "ok", self.error)

    def result(self, state: str, error: str) -> dict:
        items = outbox_items(self.root, self.repo)
        return {"state": state, "error": error, "queued": len(items), "rejected": self.cache["rejected"],
                "rejections": self.cache["rejections"], "visibility": self.cache["visibility"],
                "polled_at": self.cache["polled_at"], "senders": sorted(self.cache["senders"]),
                "errors": sorted({item["last_error"] for item in items if item.get("last_error")})}

    def checkout(self) -> Optional[Path]:
        """The cwd of a live registered session in the repository, else the newest checkout the outbox records,
        that still exists and whose origin still names the repository."""
        live = sorted((found for name, found in self.sessions.items() if name in self.live),
                      key=lambda found: found.get("updated_at") or "", reverse=True)
        recorded = read_outbox(repo_file(self.root, "outbox", self.repo))["checkouts"]
        for cwd in [found.get("cwd") for found in live] + [found.get("cwd") for found in recorded]:
            if isinstance(cwd, str) and origin_is(Path(cwd), self.repo):
                return Path(cwd)
        return None

    def delete(self, ref: str) -> None:
        self.changes.append((ref, None, {}))

    # Poll and open

    def key_ids(self) -> list[str]:
        """The key whose refs a poll fetches, and each other key the cache holds this machine's agents' refs under,
        so the refs of a key this machine gave up can still be found and deleted."""
        others = {parsed[1] for ref in self.cache["remote"] if (parsed := parse_ref(ref)) and parsed[2] in self.relays}
        current = [self.held] if self.held else []
        return current + sorted(others - set(current))

    def prefixes(self, key_ids: Optional[list[str]] = None) -> tuple[str, ...]:
        if key_ids is None:
            assert self.key is not None
            key_ids = [self.key.id]
        found = tuple(prefix for key_id in key_ids for prefix in (f"{AGENTS}{key_id}/", f"{INBOX}{key_id}/"))
        # The repository inbox is fetched only for the key agent.key names: a key given up has no inbox to show.
        if self.held in key_ids:
            found += (f"{USER}{self.held}/", f"{USER_STATE}{self.held}/")
        return found

    def fetch(self, cwd: Path, key_ids: list[str]) -> None:
        prefixes = self.prefixes(key_ids)
        done = git(cwd, "fetch", "--prune", "--quiet", "--no-write-fetch-head", REMOTE,
                   *[f"+{prefix}*:{tracking(prefix)}*" for prefix in prefixes])
        if done.returncode != 0:
            raise RelayError(f"git fetch failed: {last_line(done)}")
        listed = checked(cwd, "for-each-ref", "--format=%(objectname) %(refname)",
                         *[tracking(prefix) for prefix in prefixes])
        fetched = {}
        for line in listed.splitlines():
            sha, name = line.split(" ", 1)
            fetched[name.replace(f"refs/projector/remotes/{REMOTE}/", "refs/projector/", 1)] = sha
        kept = {ref: sha for ref, sha in self.cache["remote"].items() if not ref.startswith(prefixes)}
        self.cache["remote"] = {**kept, **fetched}
        self.cache["seen"] = {ref: seen for ref, seen in self.cache["seen"].items() if ref in self.cache["remote"]}
        if self.key:
            self.cache["agents"] = {relay_id: agent for relay_id, agent in self.cache["agents"].items()
                                    if presence_ref(self.key.id, relay_id) in fetched}
        self.cache["polled_at"] = watch.stamp(self.now)

    def rebase(self, cwd: Path) -> None:
        """Give each change whose lease failed the commit the fetch found, so it lands on a later push. A deletion
        of a ref another machine owns is dropped instead: the ref moved, so that machine renewed it. A repository
        inbox entry's deletion is dropped too, since another machine or the peer already deleted or replaced it.
        A state record is merged with the record the fetch found, so neither machine's change is lost."""
        path = repo_file(self.root, "outbox", self.repo)
        remote = self.cache["remote"]
        fetched = self.prefixes(self.key_ids())
        with locked(path.parent):
            box = read_outbox(path)
            items = []
            for item in box["items"]:
                if item.get("stale"):
                    parsed = parse_ref(item["ref"])
                    if item["sealed"] is None and (parsed is None or parsed[2] not in self.relays):
                        if not item["ref"].startswith(fetched):
                            # A ref in a peer's namespace, which this machine does not fetch, is gone or not ours.
                            self.cache["remote"].pop(item["ref"], None)
                            self.cache["seen"].pop(item["ref"], None)
                        continue
                    if parsed and parsed[0] == "state" and item["sealed"] is not None:
                        if self.key is None:
                            items.append(item)
                        elif remote.get(item["ref"]) is not None:
                            found = remote[item["ref"]]
                            item.update(sealed=self.merged_state(cwd, found, item["sealed"]), old=found, stale=False)
                            items.append(item)
                        # With no record at the ref, the entry was deleted, and its state goes with it.
                        continue
                    item.update(old=remote.get(item["ref"]), stale=False)
                    if item["sealed"] is None and item["old"] is None:
                        continue
                items.append(item)
            box["items"] = items
            write_json(path, box)

    def merged_state(self, cwd: Path, sha: str, queued: dict) -> dict:
        """The state record queued, merged with the one origin holds at `sha`, sealed again."""
        assert self.key is not None
        _, plaintext = crypto.unseal(self.key.private, queued)
        mine = json.loads(plaintext)
        try:
            _, found = self.open(cwd, self.sealed_blob(cwd, sha))
        except Rejected:
            found = None
        if found is not None and found.get("id") == mine["id"] and repo_inbox.well_formed_state(found):
            mine = repo_inbox.merge(found, mine)
            repo_inbox.take_state(self.root, self.repo, mine)
        return seal(self.key, mine)

    def examine(self, cwd: Path) -> None:
        """Open at most `OPEN_LIMIT` new records, checking each ref's name and tree before reading its blob, so a
        flood of refs delays the relay without stalling it."""
        prefixes = self.prefixes()
        seen = self.cache["seen"]
        new = sorted(ref for ref, sha in self.cache["remote"].items()
                     if ref.startswith(prefixes) and (seen.get(ref) or {}).get("sha") != sha)
        opened = 0
        # Replies whose sent copy this pass has yet to open, examined again once the others are.
        waiting: list[tuple[str, str, str, bytes, dict]] = []
        for ref in new[:EXAMINE_LIMIT]:
            if opened >= OPEN_LIMIT:
                break
            sha = self.cache["remote"][ref]
            parsed = parse_ref(ref)
            if parsed is None:
                # A ref whose name is outside the namespace is kept, since a later version could have written it.
                seen[ref] = {"sha": sha, "kind": "ignored"}
                self.note_rejection(ref, "ref-name")
                continue
            try:
                blob = self.sealed_blob(cwd, sha)
                opened += 1
                kind, _, name = parsed
                if kind == "user":
                    sender, record = self.open(cwd, blob, self.user_senders())
                    if not self.take_user(ref, sha, name, sender, record):
                        waiting.append((ref, sha, name, sender, record))
                    continue
                _, record = self.open(cwd, blob)
                if kind == "agent":
                    self.take_presence(ref, sha, name, record)
                elif kind == "message":
                    self.take_message(ref, sha, name, record)
                else:
                    self.take_state(ref, sha, name, record)
            except Rejected as rejected:
                self.reject(ref, sha, rejected)
            except ProjectorError:
                raise
            except RECORD_ERRORS as error:
                self.reject(ref, sha, Rejected("unexpected"), type(error).__name__)
        for ref, sha, name, sender, record in waiting:
            try:
                # A reply whose sent copy is still unopened, past this job's limits, waits for a later job.
                self.take_user(ref, sha, name, sender, record)
            except Rejected as rejected:
                self.reject(ref, sha, rejected)
            except ProjectorError:
                raise
            except RECORD_ERRORS as error:
                self.reject(ref, sha, Rejected("unexpected"), type(error).__name__)
        self.prune_mirror()
        repo_inbox.wake(self.root, self.repo, self.live, self.machine, self.now)

    def user_senders(self) -> set[bytes]:
        assert self.key is not None
        return {self.key.public, *(public for _, public in self.peers.values())}

    def take_user(self, ref: str, sha: str, entry_id: str, sender: bytes, record: dict) -> bool:
        """Mirror a repository inbox entry: a sent copy of your own, or a peer's item or reply. Answers False for a
        reply whose sent copy the fetch found but no job has opened yet, which is examined again later."""
        assert self.key is not None
        if record.get("id") != entry_id:
            raise Rejected("message-id-mismatch")
        location = record.get("location")
        if not isinstance(location, str) or location.lower() != self.repo.lower():
            raise Rejected("location-mismatch")
        key_id = crypto.key_id(sender)
        if sender == self.key.public:
            if not repo_inbox.well_formed_sent(record, self.repo):
                raise Rejected("field-shape")
            role, peer = "sent", (self.peers.get(record["to"]) or (None,))[0]
        elif "answers" in record:
            if not repo_inbox.well_formed_reply(record):
                raise Rejected("field-shape")
            asked = repo_inbox.by_id(self.root, self.repo, record["answers"], "sent")
            if asked is None:
                answered = user_ref(self.key.id, record["answers"])
                if (answered in self.cache["remote"]
                        and (self.cache["seen"].get(answered) or {}).get("sha") != self.cache["remote"][answered]):
                    return False
                raise Rejected("no-sent-copy")
            if asked["to"] != key_id:
                raise Rejected("sender-mismatch")
            kept = {entry["id"] for entry in repo_inbox.entries(self.root, self.repo) if entry["role"] == "sent"}
            if not repo_inbox.count_reply(self.root, record["answers"], key_id, self.peer_limit, kept, self.now):
                raise Rejected("peer-limit")
            role, peer = "reply", self.peers[key_id][0]
        else:
            if not repo_inbox.well_formed_item(record, self.repo):
                raise Rejected("field-shape")
            if not repo_inbox.count_item(self.root, key_id, self.peer_limit, self.now):
                raise Rejected("peer-limit")
            role, peer = "incoming", self.peers[key_id][0]
        if repo_inbox.mirror(self.root, self.repo, record, role=role, peer=peer, key_id=key_id, now=self.now) is None:
            raise Rejected("collision")
        sent = max(record["sent_at"], record.get("reopened_at") or 0) / 1000
        self.cache["seen"][ref] = {"sha": sha, "kind": "user", "at": min(sent, self.now)}
        return True

    def take_state(self, ref: str, sha: str, entry_id: str, record: dict) -> None:
        if record.get("id") != entry_id:
            raise Rejected("message-id-mismatch")
        location = record.get("location")
        if not isinstance(location, str) or location.lower() != self.repo.lower():
            raise Rejected("location-mismatch")
        if not repo_inbox.well_formed_state(record):
            raise Rejected("field-shape")
        repo_inbox.take_state(self.root, self.repo, record)
        self.cache["seen"][ref] = {"sha": sha, "kind": "state"}

    def prune_mirror(self) -> None:
        """Remove each mirrored entry whose ref is gone from origin and is not waiting to be pushed, as when another
        of your machines dismissed it or the peer closed it."""
        assert self.key is not None
        prefix = f"{USER}{self.key.id}/"
        queued = [item["ref"] for item in outbox_items(self.root, self.repo) if item["sealed"] is not None]
        present = {ref[len(prefix):] for ref in [*self.cache["remote"], *queued] if ref.startswith(prefix)}
        self.forget_entries([entry["id"] for entry in repo_inbox.entries(self.root, self.repo)
                             if entry["id"] not in present])

    def forget_entries(self, ids: list[str]) -> None:
        """Remove entries from the mirror, and delete their state records with them."""
        assert self.key is not None
        for entry_id in ids:
            self.delete(state_ref(self.key.id, entry_id))
        repo_inbox.forget(self.root, self.repo, ids)

    def sealed_blob(self, cwd: Path, sha: str) -> str:
        """The blob of a commit whose tree is exactly one `sealed.json` of at most 32 KiB."""
        done = git(cwd, "ls-tree", "-l", sha)
        lines = text(done.stdout).splitlines()
        found = TREE.fullmatch(lines[0]) if done.returncode == 0 and len(lines) == 1 else None
        if found is None:
            raise Rejected("tree-shape")
        if int(found[2]) > MAX_SEALED:
            raise Rejected("too-large")
        return found[1]

    def open(self, cwd: Path, blob: str, senders: Optional[set[bytes]] = None) -> tuple[bytes, dict]:
        """The key that sealed a blob, and the record the blob holds, when it opens with this machine's key and was
        sealed by one of `senders`, by default this machine's own key."""
        assert self.key is not None
        done = git(cwd, "cat-file", "blob", blob)
        if done.returncode != 0:
            raise Rejected("unreadable")
        try:
            sender, plaintext = crypto.unseal(self.key.private, parse(done.stdout))
        except crypto.TagMismatch:
            raise Rejected("tag-mismatch") from None
        except crypto.CryptoError:
            raise Rejected("not-sealed") from None
        if sender not in (senders or {self.key.public}):
            raise Rejected("other-key", sender)
        record = parse(plaintext)
        if not isinstance(record, dict):
            raise Rejected("field-shape")
        return sender, record

    def reject(self, ref: str, sha: str, rejected: Rejected, error: Optional[str] = None) -> None:
        self.cache["seen"][ref] = {"sha": sha, "kind": "rejected"}
        self.note_rejection(ref, rejected.reason, rejected.sender, error)
        self.delete(ref)

    def note_rejection(self, ref: str, reason: str, sender: Optional[bytes] = None,
                       error: Optional[str] = None) -> None:
        """Count a rejection, and keep its ref, reason, and time, but nothing the record holds. A record sealed by
        another key keeps that key's ID, and a key that is no peer of yours is kept among the unknown senders, so
        `project agent peer list` can name it. An `unexpected` rejection keeps the name of the error's class, but
        not its message, which could quote the record."""
        rejection = {"ref": ref, "reason": reason, "at": watch.stamp(self.now)}
        if error is not None:
            rejection["error"] = error
        if sender is not None:
            key_id = crypto.key_id(sender)
            rejection["key_id"] = key_id
            if key_id not in self.peers:
                senders = {**self.cache["senders"], key_id: {"public_key": crypto.encode(sender),
                                                             "at": watch.stamp(self.now)}}
                newest = sorted(senders.items(), key=lambda pair: pair[1]["at"])[-KEEP_SENDERS:]
                self.cache["senders"] = dict(newest)
        self.cache["rejected"] += 1
        self.cache["rejections"] = [*self.cache["rejections"], rejection][-KEEP_REJECTIONS:]
        self.rejections.append(rejection)

    def take_presence(self, ref: str, sha: str, relay_id: str, record: dict) -> None:
        expires = record.get("expires_at")
        if record.get("relay_id") != relay_id:
            raise Rejected("relay-id-mismatch")
        if (not isinstance(expires, (int, float)) or not isinstance(record.get("session"), str)
                or not SESSION.fullmatch(record["session"]) or not isinstance(record.get("machine"), str)
                or not agents.well_formed_presence(record)):
            raise Rejected("field-shape")
        self.cache["seen"][ref] = {"sha": sha, "kind": "agent", "expires_at": expires}
        if record["machine"] != self.machine:
            self.cache["agents"][relay_id] = record

    def take_message(self, ref: str, sha: str, relay_id: str, record: dict) -> None:
        sent = watch.seconds_of(record.get("sent_at"))
        if record.get("to") != relay_id:
            raise Rejected("recipient-mismatch")
        if record.get("id") != ref.rsplit("/", 1)[1]:
            raise Rejected("message-id-mismatch")
        if sent is None or not agents.well_formed(record):
            raise Rejected("field-shape")
        if self.now - sent >= agents.KEEP:
            raise Rejected("expired")
        self.cache["seen"][ref] = {"sha": sha, "kind": "message", "to": relay_id, "sent_at": sent}
        session = self.relays.get(relay_id)
        if session is None or session not in self.live:
            return
        if agents.deliver(self.root, session, record, self.now) == "collision":
            self.note_rejection(ref, "collision")
        self.delete(ref)

    def sweep(self) -> None:
        """Delete presence whose lease expired, and messages older than 7 days or to an agent whose presence
        expired, whichever machine left them."""
        seen = self.cache["seen"]
        expired = {parse_ref(ref)[2] for ref, found in seen.items()
                   if found.get("kind") == "agent" and found["expires_at"] <= self.now}
        present = {parse_ref(ref)[2] for ref in self.cache["remote"] if ref.startswith(AGENTS)}
        stale: list[str] = []
        for ref, found in seen.items():
            if found.get("kind") == "agent" and found["expires_at"] <= self.now:
                self.delete(ref)
            elif found.get("kind") == "message" and (self.now - found["sent_at"] >= agents.KEEP
                                                     or found["to"] in expired or found["to"] not in present):
                self.delete(ref)
            elif found.get("kind") == "user" and self.now - found.get("at", self.now) >= agents.KEEP:
                # An entry in your inbox, or one you sent to a peer's, a week after it was sent.
                self.delete(ref)
                if ref.startswith(f"{USER}{self.held}/"):
                    stale.append(ref.rsplit("/", 1)[1])
        self.forget_entries(stale)
        # A state record outlives its entry only when the entry's deletion landed without it.
        queued = {item["ref"] for item in outbox_items(self.root, self.repo) if item["sealed"] is not None}
        for ref in self.cache["remote"]:
            parsed = parse_ref(ref)
            if parsed and parsed[0] == "state" and parsed[1] == self.held:
                entry = user_ref(parsed[1], parsed[2])
                if (entry not in self.cache["remote"] and entry not in queued) or \
                        (self.now - (seen.get(entry) or {}).get("at", self.now) >= agents.KEEP):
                    self.delete(ref)

    def read_visibility(self) -> None:
        result = self.gh(["repo", "view", self.repo, "--json", "visibility"])
        try:
            found = json.loads(result.stdout).get("visibility") if result.returncode == 0 else None
        except (ValueError, AttributeError):
            found = None
        if isinstance(found, str) and found:
            self.cache["visibility"] = found.upper()

    # Renew and clean up this machine's agents

    def maintain(self) -> None:
        """Renew each live agent's presence before its lease runs out, and delete the refs of each agent that
        lapsed, and of every agent under a key this machine no longer holds."""
        queued = {item["ref"] for item in outbox_items(self.root, self.repo)}
        for ref in self.cache["remote"]:
            parsed = parse_ref(ref)
            if parsed and parsed[2] in self.relays and parsed[1] != self.held:
                self.delete(ref)
        if self.key is None:
            return
        for name, found in self.sessions.items():
            relay_id = found["relay_id"]
            ref = presence_ref(self.key.id, relay_id)
            # A session can unregister while this job runs, so its mark is checked again here.
            if name in self.live and not agents.unregistered(watch.session_folder(self.root, name)):
                record = presence(self.root, name, found, self.now)
                seen = self.cache["seen"].get(ref) or {}
                expires = seen.get("expires_at")
                # A presence says what the subscriptions and adoptions say now, so one that changes, as when a
                # review of the repository adopts a pull request, is pushed again without waiting for its renewal.
                due = ref not in self.cache["remote"] or not isinstance(expires, (int, float)) \
                    or expires - self.now < RENEW or found.get("relay") == "pending" \
                    or seen.get("digest") not in (None, digest(record))
                if due and ref not in queued:
                    self.changes.append((ref, seal(self.key, record), presence_meta(record)))
                continue
            self.delete(ref)
            for other in self.cache["remote"]:
                parsed = parse_ref(other)
                if parsed and parsed[0] == "message" and parsed[2] == relay_id:
                    self.delete(other)

    # Push

    def push(self, cwd: Path) -> None:
        path = repo_file(self.root, "outbox", self.repo)
        with locked(path.parent):
            box = read_outbox(path)
            snapshot = [dict(item) for item in box["items"]]
            self.attempted = pending(box)
            if not snapshot:
                return
            box["pushing"] = [item["ref"] for item in snapshot]
            write_json(path, box)
        commits: dict[str, str] = {}
        results: dict[str, tuple[bool, str]] = {}
        try:
            for item in snapshot:
                if item["sealed"] is not None:
                    commits[item["ref"]] = write_commit(cwd, item["sealed"])
            # An item you sent to a peer is published again with no lease: only your machines write it, and the
            # peer may have deleted it since this machine last pushed it.
            forced = {item["ref"] for item in snapshot
                      if item["sealed"] is not None and (item.get("meta") or {}).get("force")}
            leases = [f"--force-with-lease={item['ref']}:{item['old']}" for item in snapshot
                      if item["old"] and item["ref"] not in forced]
            specs = [f"{'+' if item['ref'] in forced else ''}{commits[item['ref']]}:{item['ref']}"
                     if item["sealed"] is not None else f":{item['ref']}" for item in snapshot]
            done = git(cwd, "push", "--porcelain", *leases, REMOTE, *specs, timeout=PUSH_TIMEOUT)
            results = push_results(text(done.stdout))
            failed = f"git push failed: {last_line(done)}"
        except RelayError as error:
            failed = str(error)
        landed = self.settle(path, snapshot, commits, results, failed)
        self.record(cwd, landed, snapshot)
        if any(not results.get(item["ref"], (False, ""))[0] for item in snapshot):
            self.error = self.error or failed

    def settle(self, path: Path, snapshot: list[dict], commits: dict[str, str],
               results: dict[str, tuple[bool, str]], failed: str) -> dict[str, Optional[str]]:
        """Remove each item that landed and has not changed since the push took it, give one that changed the
        pushed commit as its new `old`, and keep each failure with its error. Answers what landed."""
        landed: dict[str, Optional[str]] = {}
        with locked(path.parent):
            box = read_outbox(path)
            items = {item["ref"]: item for item in box["items"]}
            for taken in snapshot:
                ref = taken["ref"]
                ok, said = results.get(ref, (False, failed))
                item = items.get(ref)
                if ok:
                    landed[ref] = commits.get(ref)
                    if item is not None and item["sealed"] == taken["sealed"]:
                        del items[ref]
                        continue
                    if item is not None:
                        item.update(old=commits.get(ref), last_error="", stale=False)
                elif item is not None:
                    item.update(last_error=said, stale=any(word in said for word in STALE))
                if item is not None and item["sealed"] is None and item["old"] is None:
                    del items[ref]
            box["items"] = list(items.values())
            box["pushing"] = []
            queued_from = {item.get("cwd") for item in box["items"]}
            box["checkouts"] = [found for index, found in enumerate(box["checkouts"])
                                if index == 0 or found.get("cwd") in queued_from]
            write_json(path, box)
        return landed

    def record(self, cwd: Path, landed: dict[str, Optional[str]], snapshot: list[dict]) -> None:
        """Move each landed ref's tracking ref and the cache to what the push left on origin, and mark each
        landed presence of this machine's sessions published."""
        if not landed:
            return
        metas = {item["ref"]: item.get("meta") or {} for item in snapshot}
        lines = "".join(f"update {tracking(ref)} {sha}\n" if sha else f"delete {tracking(ref)}\n"
                        for ref, sha in landed.items())
        git(cwd, "update-ref", "--stdin", input=lines.encode())
        published: dict[str, Optional[str]] = {}
        for ref, sha in landed.items():
            parsed = parse_ref(ref)
            if sha is None:
                self.cache["remote"].pop(ref, None)
                self.cache["seen"].pop(ref, None)
                if parsed and parsed[0] == "agent":
                    self.cache["agents"].pop(parsed[2], None)
                continue
            self.cache["remote"][ref] = sha
            self.cache["seen"][ref] = {"sha": sha, **metas.get(ref, {})}
            if parsed and parsed[0] == "agent" and parsed[2] in self.relays:
                published[self.relays[parsed[2]]] = metas.get(ref, {}).get("digest")
        for name, landed_digest in published.items():
            agents.mark_published(self.root, name, landed_digest)


# The daemon's side

class Relay:
    """The relay's state in the watch daemon: the key, read once on the worker pool and again only when
    `agent.key` changes or after a failed read, and each repository's job and its last result."""

    def __init__(self, root: Path, *, gh: Callable[[list[str]], watch.GhResult], wall: Callable[[], float],
                 log: Callable[[str], None], environ: Optional[dict] = None) -> None:
        self.root = root
        self.gh = gh
        self.wall = wall
        self.log = log
        self.environ = dict(os.environ) if environ is None else environ
        self.pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="relay")
        self.jobs: dict[str, tuple[Job, Future]] = {}
        self.results: dict[str, dict] = {}
        # The changes each repository's last job tried to push, so a change that keeps failing waits for the next
        # poll instead of pushing every second, while one queued as that job ran starts the next job at once.
        self.settled: dict[str, frozenset[tuple[str, str]]] = {}
        self.key: Optional[Key] = None
        self.value: Optional[str] = None
        self.key_error = NO_KEY
        self.reading: Optional[tuple[str, Future]] = None
        self.signature: object = ()
        self.last_full: Optional[float] = None
        self.fresh = False
        # The repositories whose job owes a poll since the key was read: a repository whose job was still running
        # then gets it as soon as that job ends, so a registration waiting for the key need not wait for a pass.
        self.owed: dict[str, str] = {}
        self.was_live: set[str] = set()
        # The peers and agent.peer_limit, read from ~/.projector.toml again whenever the file changes.
        self.peers: dict[str, bytes] = {}
        self.peer_limit = agents.PEER_LIMIT
        self.peers_error = ""
        self.peers_signature: object = ()

    def start(self) -> None:
        """Forget the push each outbox records as running: only this daemon pushes, and it has not started."""
        for path in (self.root / "outbox").glob("*/*.json"):
            with locked(path.parent):
                box = read_outbox(path)
                if box["pushing"]:
                    write_json(path, {**box, "pushing": []})

    def busy(self) -> bool:
        return any(not future.done() for _, future in self.jobs.values()) or self.reading is not None

    def tick(self, live: set[str], polled: bool, full_every: float) -> bool:
        """Read finished jobs, follow `agent.key`, and start each due repository's job. Answers whether the
        status the daemon reports changed."""
        before = self.status()
        now = self.wall()
        full = polled and (self.last_full is None or now - self.last_full >= full_every)
        if full:
            self.last_full = now
        self.collect()
        self.check_key(full)
        self.check_peers()
        records, gone = agents.relayed(self.root)
        live = live - gone
        lapsed = self.was_live - live
        self.was_live = set(live)
        if self.fresh:
            self.owed.update(self.repositories(records, True, lapsed))
            self.fresh = False
        for name, repo in sorted({**self.repositories(records, polled, lapsed), **self.owed}.items()):
            if name in self.jobs:
                continue
            owed = self.owed.pop(name, None) is not None
            here = {session: found for session, found in records.items() if (found.get("repo") or "").lower() == name}
            remote = read_cache(self.root, repo)["remote"]
            lingering = {found.get("relay_id") for session, found in here.items() if session not in live}
            poll = (polled or owed) and (any(session in live for session in here)
                                         or any((parsed := parse_ref(ref)) and parsed[2] in lingering
                                                for ref in remote))
            job = Job(self.root, repo, self.key, set(live), now, poll=poll, full=full, gh=self.gh,
                      held=self.held(), peers=self.peers, peer_limit=self.peer_limit)
            self.jobs[name] = (job, self.pool.submit(job.run))
        return self.status() != before

    def repositories(self, records: dict[str, dict], polled: bool, lapsed: set[str]) -> dict[str, str]:
        """The repositories whose job runs now, lowercased, with their spelling: on a poll, every repository a
        registered session here works in, and otherwise each one with a queued change its last job did not try to
        push, or with a session that just lapsed."""
        found = {}
        for path in (self.root / "outbox").glob("*/*.json"):
            name = f"{path.parent.name}/{path.stem}"
            box = read_outbox(path)
            if box["items"] and (polled or name not in self.settled or pending(box) - self.settled[name]):
                found[name] = name
        for name, record in records.items():
            repo = record.get("repo")
            if repo and (polled or name in lapsed) and (
                    self.key is not None or repo_file(self.root, "relay", repo).exists()):
                found[repo.lower()] = repo
        return found

    def collect(self) -> None:
        for repo, (job, future) in list(self.jobs.items()):
            if future.done():
                del self.jobs[repo]
                self.settled[repo] = job.attempted
                for rejection in job.rejections:
                    error = f" ({rejection['error']})" if rejection.get("error") else ""
                    self.log(f"relay {job.repo}: rejected {rejection['ref']}: {rejection['reason']}{error}, at "
                             f"{rejection['at']}")
                try:
                    self.results[repo] = future.result()
                except Exception as error:
                    self.results[repo] = {"state": "error", "error": f"{type(error).__name__}: {error}"}
        if self.reading is not None and self.reading[1].done():
            value, job = self.reading
            self.reading = None
            if value == self.value:
                try:
                    self.key, self.key_error = job.result(), ""
                    self.fresh = True
                except (ConfigError, KeychainError, RelayError) as error:
                    self.key, self.key_error = None, str(error)

    def check_key(self, full: bool) -> None:
        signature = watch.file_signature(user_config_path(self.environ))
        if signature != self.signature:
            self.signature = signature
            try:
                value = configured(self.environ)
                error = "" if value else NO_KEY
            except ConfigError as problem:
                value, error = None, str(problem)
            if value != self.value or error:
                self.value, self.key, self.key_error = value, None, error
                if value:
                    self.submit_read()
                return
        if full and self.value and self.key is None and self.reading is None:
            self.submit_read()

    def held(self) -> Optional[str]:
        try:
            return value_id(self.value) if self.value else None
        except ConfigError:
            return None

    def submit_read(self) -> None:
        assert self.value is not None
        self.reading = (self.value, self.pool.submit(read_key, self.value, keychain.TIMEOUT))

    def check_peers(self) -> None:
        """Read the peers and their limit when `~/.projector.toml` changed, which reads no keychain. A table that
        does not read leaves the relay with no peers, and says why in `watch status`."""
        signature = watch.file_signature(user_config_path(self.environ))
        if signature == self.peers_signature:
            return
        self.peers_signature = signature
        try:
            self.peers, self.peer_limit, self.peers_error = (config.peers(self.environ),
                                                             repo_inbox.peer_limit(self.environ), "")
        except ConfigError as error:
            self.peers, self.peer_limit, self.peers_error = {}, agents.PEER_LIMIT, str(error)

    def status(self) -> dict:
        state = "on" if self.key else ("reading" if self.reading else "off")
        return {"key": {"state": state, "id": self.held(), "store": self.key.store if self.key else None,
                        "error": self.key_error if not self.key and not self.reading else ""},
                "peers": {"count": len(self.peers), "error": self.peers_error},
                "repositories": dict(sorted(self.results.items()))}

    def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for every running job and key read, as a test or the daemon's exit does."""
        for job in [*(future for _, future in self.jobs.values()), *([self.reading[1]] if self.reading else [])]:
            try:
                job.result(timeout=timeout)
            except Exception:
                pass

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
