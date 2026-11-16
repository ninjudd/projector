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
    relay/<owner>/<repo>.json      the repository's visibility, the rejected count, the key's refs on origin, what
                                   each held when it was opened, and other machines' agents

Refs in the repository, each a parentless commit whose tree holds one `sealed.json`:

    refs/projector/agents/<key-id>/<relay-id>          a session's presence, with when its lease expires
    refs/projector/inbox/<key-id>/<relay-id>/<id>      a message to the session with that relay ID
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import agents, config, crypto, keychain, review, summary, watch
from .config import INLINE_KEY, KEYCHAIN, ConfigError, inline_key, unset_value, user_config_path, write_value
from .core import ProjectorError, run_bounded
from .inbox import SESSION, locked, read_json, write_json
from .keychain import KeychainError

REMOTE = "origin"
AGENTS = "refs/projector/agents/"
INBOX = "refs/projector/inbox/"
KEY_ID = re.compile(r"^[0-9a-f]{16}$")
PRESENCE_REF = re.compile(r"^refs/projector/agents/([0-9a-f]{16})/([0-9a-f]{16})$")
INBOX_REF = re.compile(r"^refs/projector/inbox/([0-9a-f]{16})/([0-9a-f]{16})/(m-[0-9a-f]{32})$")
TREE = re.compile(r"^100644 blob ([0-9a-f]{40,64}) +([0-9]+)\tsealed\.json$")
LEASE = 3600.0
RENEW = 1800.0
MAX_SEALED = 32 * 1024
# A sealed record and the record inside it are flat objects, so JSON nested deeper was planted and is not parsed.
MAX_DEPTH = 8
STRING = re.compile(rb'"(?:[^"\\]|\\.)*"')
BRACKET = re.compile(rb"[\[\]{}]")
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
COPY = ("To copy the key to another machine, run `project agent key export` here in a terminal, then run "
        "`project agent key import` there in a terminal and paste the line it printed.")


class RelayError(ProjectorError):
    """A relay key or a Git command that the relay cannot use."""


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
    if value.startswith(KEYCHAIN):
        return value[len(KEYCHAIN):]
    return crypto.key_id(crypto.public_key(inline_key(value)))


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


def save_key(root: Path, private: bytes, *, inline: bool, environ: Optional[dict] = None) -> tuple[str, str, str]:
    """Store the private key in the keychain when one is usable, and inline otherwise or with `inline`; write
    `agent.key` to `~/.projector.toml` with mode 0600. Answers the key ID, where the key is stored, and why when
    that is inline."""
    key_id = crypto.key_id(crypto.public_key(private))
    secret = crypto.encode(private)
    value, store, reason = INLINE_KEY + secret, "inline", "you passed --inline"
    if not inline:
        try:
            store = keychain.store(key_id, secret)
            value, reason = KEYCHAIN + key_id, ""
        except KeychainError as error:
            reason = str(error)
    path = user_config_path(environ)
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    if os.name == "posix":
        os.chmod(path, 0o600)
    write_value(path, "agent.key", value)
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "key.json", {"schema_version": 1, "key_id": key_id, "store": store, "reason": reason})
    return key_id, store, reason


def init_key(root: Path, *, inline: bool, environ: Optional[dict] = None) -> tuple[str, str, str]:
    existing = configured(environ)
    if existing:
        raise RelayError(f"agent.key already holds key {value_id(existing)}; run `project agent key remove` "
                         "first to make a new one, which no message sealed to the old key can be opened with")
    return save_key(root, crypto.new_private_key(), inline=inline, environ=environ)


def parse_line(line: str) -> bytes:
    found = re.fullmatch(r'\s*(?:agent\.key\s*=\s*)?"?(x25519:[A-Za-z0-9+/=]+)"?\s*', line)
    if found is None:
        raise RelayError("the line is not the agent.key line `project agent key export` prints")
    return inline_key(found.group(1))


def import_key(root: Path, line: str, *, inline: bool, replace: bool,
               environ: Optional[dict] = None) -> tuple[str, str, str]:
    private = parse_line(line)
    existing = configured(environ)
    wanted = crypto.key_id(crypto.public_key(private))
    if existing and value_id(existing) != wanted:
        if not replace:
            raise RelayError(f"agent.key already holds a different key, {value_id(existing)}; pass --replace to "
                             "replace it, after which no message sealed to it can be opened")
        if existing.startswith(KEYCHAIN):
            try:
                keychain.delete(value_id(existing))
            except KeychainError:
                pass
    return save_key(root, private, inline=inline, environ=environ)


def export_line(environ: Optional[dict] = None) -> str:
    value = configured(environ)
    if not value:
        raise RelayError(NO_KEY)
    return f'agent.key = "{INLINE_KEY}{crypto.encode(read_key(value).private)}"'


def show_key(environ: Optional[dict] = None) -> dict:
    value = configured(environ)
    if not value:
        raise RelayError(NO_KEY)
    key = read_key(value)
    return {"key_id": key.id, "public_key": crypto.encode(key.public), "store": key.store}


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


def parse_ref(ref: str) -> Optional[tuple[str, str, str]]:
    """A relay ref's kind, `agent` or `message`, its key ID, and the relay ID it is for."""
    if found := PRESENCE_REF.fullmatch(ref):
        return "agent", found[1], found[2]
    if found := INBOX_REF.fullmatch(ref):
        return "message", found[1], found[2]
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


def presence(session: str, found: dict, now: float) -> dict:
    record = {key: value for key, value in found.items() if key not in ("relay", "ended_at")}
    return {**record, "session": session, "expires_at": now + LEASE}


def seal(key: Key, record: dict) -> dict:
    return crypto.seal(key.private, key.public, json.dumps(record, ensure_ascii=False, sort_keys=True).encode())


def publish(root: Path, session: str, found: dict, key: Key, now: float) -> None:
    """Queue the session's sealed presence."""
    record = presence(session, found, now)
    queue(root, found["repo"], found["cwd"], [(presence_ref(key.id, found["relay_id"]), seal(key, record),
                                               {"kind": "agent", "expires_at": record["expires_at"]})], now)


def queue_message(root: Path, repo: str, cwd: str, key: Key, message: dict, now: float) -> None:
    meta = {"kind": "message", "to": message["to"], "sent_at": watch.seconds_of(message["sent_at"])}
    queue(root, repo, cwd, [(inbox_ref(key.id, message["to"], message["id"]), seal(key, message), meta)], now)


# Git

def git(cwd: Path, *args: str, input: Optional[bytes] = None, env: Optional[dict] = None,
        timeout: float = FETCH_TIMEOUT) -> subprocess.CompletedProcess:
    """Run Git in `cwd` with standard input closed or fed `input`, no terminal prompt, and a timeout after which
    its process group is killed."""
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        return run_bounded(["git", *args], cwd=cwd, input=input, env=environment, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RelayError(f"git {args[0]} did not finish within {timeout:g} seconds, so it was stopped") from None
    except OSError as error:
        raise RelayError(f"git could not start: {error}") from None


def shallow(data: bytes) -> bool:
    """Whether the JSON in `data` nests arrays and objects at most `MAX_DEPTH` deep, counting the brackets
    outside its strings, so `json.loads` never recurses deeply into a blob that anyone who can push planted."""
    depth = 0
    for found in BRACKET.finditer(STRING.sub(b'""', data)):
        depth += 1 if found[0] in (b"[", b"{") else -1
        if depth > MAX_DEPTH:
            return False
    return True


def parse(data: bytes) -> object:
    """The JSON an untrusted blob holds, raising `ValueError` for any that does not parse or nests too deeply."""
    if not shallow(data):
        raise ValueError(f"the JSON nests more than {MAX_DEPTH} deep")
    try:
        return json.loads(data)
    except RecursionError:
        raise ValueError("the JSON nests too deeply to parse") from None


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
    blob = checked(cwd, "hash-object", "-w", "--stdin", input=json.dumps(sealed, sort_keys=True).encode())
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
                 full: bool, gh: Callable[[list[str]], watch.GhResult], held: Optional[str] = None) -> None:
        self.root = root
        self.repo = repo
        self.key = key
        # The key agent.key names, which a locked keychain can keep the daemon from reading.
        self.held = key.id if key else held
        self.live = live
        self.now = now
        self.poll = poll
        self.full = full
        self.gh = gh
        self.cache = read_cache(root, repo)
        self.machine = agents.this_machine(root)
        self.sessions = {name: found for name, found in agents.records(root).items()
                         if (found.get("repo") or "").lower() == repo.lower()}
        self.relays = {found["relay_id"]: name for name, found in self.sessions.items()
                       if isinstance(found.get("relay_id"), str)}
        self.changes: list[tuple[str, Optional[dict], dict]] = []
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
                self.rebase()
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
                "visibility": self.cache["visibility"], "polled_at": self.cache["polled_at"],
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
        return tuple(prefix for key_id in key_ids for prefix in (f"{AGENTS}{key_id}/", f"{INBOX}{key_id}/"))

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

    def rebase(self) -> None:
        """Give each change whose lease failed the commit the fetch found, so it lands on a later push. A deletion
        of a ref another machine owns is dropped instead: the ref moved, so that machine renewed it."""
        path = repo_file(self.root, "outbox", self.repo)
        remote = self.cache["remote"]
        with locked(path.parent):
            box = read_outbox(path)
            items = []
            for item in box["items"]:
                if item.get("stale"):
                    parsed = parse_ref(item["ref"])
                    if item["sealed"] is None and (parsed is None or parsed[2] not in self.relays):
                        continue
                    item.update(old=remote.get(item["ref"]), stale=False)
                    if item["sealed"] is None and item["old"] is None:
                        continue
                items.append(item)
            box["items"] = items
            write_json(path, box)

    def examine(self, cwd: Path) -> None:
        """Open at most `OPEN_LIMIT` new records, checking each ref's name and tree before reading its blob, so a
        flood of refs delays the relay without stalling it."""
        prefixes = self.prefixes()
        seen = self.cache["seen"]
        new = sorted(ref for ref, sha in self.cache["remote"].items()
                     if ref.startswith(prefixes) and (seen.get(ref) or {}).get("sha") != sha)
        opened = 0
        for ref in new[:EXAMINE_LIMIT]:
            if opened >= OPEN_LIMIT:
                break
            sha = self.cache["remote"][ref]
            parsed = parse_ref(ref)
            if parsed is None:
                seen[ref] = {"sha": sha, "kind": "ignored"}
                continue
            blob = self.sealed_blob(cwd, sha)
            if blob is None:
                self.reject(ref, sha)
                continue
            opened += 1
            record = self.open(cwd, blob)
            kind, _, relay_id = parsed
            if kind == "agent":
                self.take_presence(ref, sha, relay_id, record)
            else:
                self.take_message(ref, sha, relay_id, record)

    def sealed_blob(self, cwd: Path, sha: str) -> Optional[str]:
        """The blob of a commit whose tree is exactly one `sealed.json` of at most 32 KiB, or None."""
        done = git(cwd, "ls-tree", "-l", sha)
        lines = text(done.stdout).splitlines()
        found = TREE.fullmatch(lines[0]) if done.returncode == 0 and len(lines) == 1 else None
        return found[1] if found and int(found[2]) <= MAX_SEALED else None

    def open(self, cwd: Path, blob: str) -> Optional[dict]:
        """The record a blob holds when it opens with this machine's key and was sealed by the same key."""
        assert self.key is not None
        done = git(cwd, "cat-file", "blob", blob)
        if done.returncode != 0 or len(done.stdout) > MAX_SEALED:
            return None
        try:
            sender, plaintext = crypto.unseal(self.key.private, parse(done.stdout))
            record = parse(plaintext)
        except (crypto.CryptoError, ValueError):
            return None
        return record if sender == self.key.public and isinstance(record, dict) else None

    def reject(self, ref: str, sha: str) -> None:
        self.cache["seen"][ref] = {"sha": sha, "kind": "rejected"}
        self.cache["rejected"] += 1
        self.delete(ref)

    def take_presence(self, ref: str, sha: str, relay_id: str, record: Optional[dict]) -> None:
        expires = (record or {}).get("expires_at")
        if (record is None or record.get("relay_id") != relay_id or not isinstance(expires, (int, float))
                or not isinstance(record.get("session"), str) or not SESSION.fullmatch(record["session"])
                or not isinstance(record.get("machine"), str) or not agents.well_formed_presence(record)):
            self.reject(ref, sha)
            return
        self.cache["seen"][ref] = {"sha": sha, "kind": "agent", "expires_at": expires}
        if record["machine"] != self.machine:
            self.cache["agents"][relay_id] = record

    def take_message(self, ref: str, sha: str, relay_id: str, record: Optional[dict]) -> None:
        sent = watch.seconds_of((record or {}).get("sent_at"))
        if (record is None or record.get("to") != relay_id or record.get("id") != ref.rsplit("/", 1)[1]
                or sent is None or self.now - sent >= agents.KEEP or not agents.well_formed(record)):
            self.reject(ref, sha)
            return
        self.cache["seen"][ref] = {"sha": sha, "kind": "message", "to": relay_id, "sent_at": sent}
        session = self.relays.get(relay_id)
        if session is None or session not in self.live:
            return
        if agents.deliver(self.root, session, record, self.now) == "collision":
            self.cache["rejected"] += 1
        self.delete(ref)

    def sweep(self) -> None:
        """Delete presence whose lease expired, and messages older than 7 days or to an agent whose presence
        expired, whichever machine left them."""
        seen = self.cache["seen"]
        expired = {parse_ref(ref)[2] for ref, found in seen.items()
                   if found.get("kind") == "agent" and found["expires_at"] <= self.now}
        present = {parse_ref(ref)[2] for ref in self.cache["remote"] if ref.startswith(AGENTS)}
        for ref, found in seen.items():
            if found.get("kind") == "agent" and found["expires_at"] <= self.now:
                self.delete(ref)
            elif found.get("kind") == "message" and (self.now - found["sent_at"] >= agents.KEEP
                                                     or found["to"] in expired or found["to"] not in present):
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
            if name in self.live:
                expires = (self.cache["seen"].get(ref) or {}).get("expires_at")
                due = ref not in self.cache["remote"] or not isinstance(expires, (int, float)) \
                    or expires - self.now < RENEW
                if due and ref not in queued:
                    record = presence(name, found, self.now)
                    self.changes.append((ref, seal(self.key, record),
                                         {"kind": "agent", "expires_at": record["expires_at"]}))
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
            leases = [f"--force-with-lease={item['ref']}:{item['old']}" for item in snapshot if item["old"]]
            specs = [f"{commits[item['ref']]}:{item['ref']}" if item["sealed"] is not None else f":{item['ref']}"
                     for item in snapshot]
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
        published = set()
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
                published.add(self.relays[parsed[2]])
        for name in published:
            agents.mark_published(self.root, name)


# The daemon's side

class Relay:
    """The relay's state in the watch daemon: the key, read once on the worker pool and again only when
    `agent.key` changes or after a failed read, and each repository's job and its last result."""

    def __init__(self, root: Path, *, gh: Callable[[list[str]], watch.GhResult], wall: Callable[[], float],
                 environ: Optional[dict] = None) -> None:
        self.root = root
        self.gh = gh
        self.wall = wall
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
        self.was_live: set[str] = set()

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
        polled = polled or self.fresh
        self.fresh = False
        lapsed = self.was_live - live
        self.was_live = set(live)
        records = agents.records(self.root)
        for name, repo in sorted(self.repositories(records, polled, lapsed).items()):
            if name in self.jobs:
                continue
            here = {session: found for session, found in records.items() if (found.get("repo") or "").lower() == name}
            remote = read_cache(self.root, repo)["remote"]
            lingering = {found.get("relay_id") for session, found in here.items() if session not in live}
            poll = polled and (any(session in live for session in here)
                               or any((parsed := parse_ref(ref)) and parsed[2] in lingering for ref in remote))
            job = Job(self.root, repo, self.key, set(live), now, poll=poll, full=full, gh=self.gh,
                      held=self.held())
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

    def status(self) -> dict:
        state = "on" if self.key else ("reading" if self.reading else "off")
        return {"key": {"state": state, "id": self.held(), "store": self.key.store if self.key else None,
                        "error": self.key_error if not self.key and not self.reading else ""},
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
