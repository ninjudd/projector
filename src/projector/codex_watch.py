"""Local Codex delivery for the shared watch daemon."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from . import inbox
from .core import ProjectorError, UsageError
from .inbox import locked, read_json, try_lock, unlock, write_json

LEASE = 24 * 3600
LIMIT = 8 * 1024
TIMEOUT = 60
RESEND = 1800
# An uncertain batch is resent after RESEND unless its transcript shows it arrived first. A copy that
# arrives after the batch is consumed finds its receipt and is blocked before it starts a turn.
SENDABLE = ("prepared", "failed", "uncertain")
UNCERTAIN = "submission outcome unknown; Projector resends it in 30 minutes unless it arrives first"
CONFIRM_EVERY = 30
MARKER = re.compile(r"^PROJECTOR WATCH ([0-9a-f-]{36})\n")
TERMINAL = {"MERGED", "CLOSED", "MISSING", "MOVED"}
# A claim lapses a minute after its last renewal, so renewing a third of the way through keeps a line that
# waits in Codex's queue claimed through a send that runs to TIMEOUT.
RENEW_AFTER_MS = 20_000


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Event(str):
    def __new__(cls, line: str, key: list):
        value = super().__new__(cls, line)
        value.key = json.dumps(key, separators=(",", ":"), ensure_ascii=False)
        return value


def registration(folder: Path) -> dict:
    return read_json(folder / "codex.json") or {}


def own_transcript(session: str, value: object) -> Optional[str]:
    """The hook's transcript when it belongs to the session's own thread. A subagent's hook carries its
    parent's session ID with the subagent's transcript, which says nothing about the parent's thread."""
    if isinstance(value, str) and "\0" not in value and Path(value).is_absolute() and session in Path(value).name:
        return value
    return None


def active(folder: Path, now: float) -> bool:
    record = registration(folder)
    expires = record.get("expires_at")
    return (record.get("schema_version") == 1 and record.get("enabled") is True
            and isinstance(expires, (int, float)) and expires > now and not gone(record))


def gone(record: dict) -> bool:
    """Whether Codex archived or deleted the conversation, which moves or removes its transcript."""
    transcript = record.get("transcript_path")
    return bool(transcript) and not Path(transcript).exists()


def pending(folder: Path) -> list[tuple[Path, dict]]:
    return [(path, record) for path in sorted((folder / "codex-pending").glob("*.json"))
            if (record := read_json(path)) and record.get("batch_id") == path.stem]


def queued(folder: Path) -> list[dict]:
    return (read_json(folder / "codex-events.json") or {}).get("items") or []


def save_queue(folder: Path, items: list[dict]) -> None:
    write_json(folder / "codex-events.json", {"schema_version": 1, "items": items})


def has_work(folder: Path) -> bool:
    from .watch import subscriptions
    return (any(not line.excluded for line in subscriptions(folder)) or bool(queued(folder) or pending(folder))
            or any(inbox.owes_a_line(item) for item in inbox_items(folder.name)))


SCANNED: dict[str, tuple[int, list[dict]]] = {}


def inbox_items(session: str) -> list[dict]:
    """The session's inbox items, read again only when its folder changed. Every inbox write replaces a file,
    which changes the folder's modification time, and the daemon asks once a second."""
    try:
        folder = inbox.session_dir(session)
        stamp = folder.stat().st_mtime_ns
    except (ProjectorError, OSError):
        return []
    seen = SCANNED.get(str(folder))
    if seen is None or seen[0] != stamp:
        seen = SCANNED[str(folder)] = (stamp, inbox.load_items(folder))
    return seen[1]


def inbox_key(item: dict) -> Optional[list]:
    """An inbox event's key, `["inbox", ref, line, token]`, or None for any other event."""
    key = json.loads(item["key"])
    return key if key[:1] == ["inbox"] and len(key) == 4 else None


def claim_of(session: str, ref: str) -> dict:
    try:
        return (read_json(inbox.item_file(inbox.session_dir(session), ref)) or {}).get("claim") or {}
    except (ProjectorError, OSError, ValueError):
        return {}


def offer(folder: Path) -> None:
    """Claim each line the session's inbox owes and queue it, so this adapter is its one deliverer."""
    lines = []
    for item in inbox_items(folder.name):
        if not inbox.owes_a_line(item) or inbox.live_claim(item):
            continue
        try:
            claimed = inbox.claim(folder.name, item["ref"])
        except inbox.InboxBusy:
            break
        except ProjectorError:
            continue
        held = claimed["claim"]
        lines.append(Event(inbox.message(claimed), ["inbox", claimed["ref"], held["line"], held["token"]]))
    if lines:
        append(folder, lines)


def unsettled(folder: Path) -> list[list]:
    return (read_json(folder / "codex-unsettled.json") or {}).get("keys") or []


def renew(folder: Path) -> None:
    """Renew the claim of every inbox line that waits in the queue, in a batch, or for its settle, so no other
    deliverer claims it while Codex has yet to take it."""
    lines = queued(folder) + [item for _, batch in pending(folder) for item in batch["items"]]
    for _, ref, _, token in [key for item in lines if (key := inbox_key(item))] + unsettled(folder):
        claim = claim_of(folder.name, ref)
        if claim.get("token") == token and inbox.now_ms() - int(claim.get("renewedAt", 0)) >= RENEW_AFTER_MS:
            with contextlib.suppress(ProjectorError):
                inbox.claim(folder.name, ref, renew=token)


def settle(folder: Path, keys: list[list]) -> None:
    """Mark each inbox line delivered. A claim that a restore, an answer, or a revision replaced settles
    nothing, and the line the item owes now goes out next. A line a busy inbox kept from settling waits for
    the next tick."""
    left = []
    for key in keys:
        try:
            inbox.settle(folder.name, key[1], key[3], delivered=True)
        except inbox.InboxBusy:
            left.append(key)
        except ProjectorError:
            pass
    waiting = [key for key in unsettled(folder) if key not in keys] + left
    if waiting or (folder / "codex-unsettled.json").exists():
        write_json(folder / "codex-unsettled.json", {"schema_version": 1, "keys": waiting})


@contextlib.contextmanager
def submission(folder: Path):
    descriptor = os.open(folder / "codex-submit.lock", os.O_CREAT | os.O_RDWR, 0o600)
    held = try_lock(descriptor)
    try:
        yield held
    finally:
        if held:
            unlock(descriptor)
        os.close(descriptor)


def sending(folder: Path) -> bool:
    with submission(folder) as held:
        return not held


def recover(folder: Path) -> None:
    keys = set()
    batches = pending(folder)
    for path, batch in batches:
        keys.update(item["key"] for item in batch["items"])
        receipt = folder / "codex-receipts" / path.name
        if receipt.exists():
            path.unlink()
        elif batch["state"] == "submitting" and not sending(folder):
            batch.update(state="uncertain", next_attempt_at=batch.get("submitted_at", 0) + RESEND,
                         last_error=UNCERTAIN)
            write_json(path, batch)
    items = queued(folder)
    remaining = [item for item in items if item["key"] not in keys]
    if remaining != items:
        save_queue(folder, remaining)


def prune_receipts(folder: Path, now: float) -> None:
    if sending(folder):
        return
    inflight = {path.name for path, _ in pending(folder)}
    for path in (folder / "codex-receipts").glob("*.json"):
        receipt = read_json(path) or {}
        if path.name not in inflight and now - receipt.get("consumed_at", now) > LEASE and not sending(folder):
            path.unlink()


def append(folder: Path, lines: list[str]) -> None:
    recover(folder)
    items = queued(folder)
    keys = {item["key"] for item in items}
    keys.update(item["key"] for _, batch in pending(folder) for item in batch["items"])
    for line in lines:
        key = getattr(line, "key", None) or json.dumps(["notice", digest(line)])
        if key not in keys:
            items.append({"key": key, "line": str(line)})
            keys.add(key)
    save_queue(folder, items)


def eligible(folder: Path, item: dict) -> bool:
    from .watch import subscriptions
    if item["line"].split(" ", 1)[0] in TERMINAL:
        return True
    key = json.loads(item["key"])
    if key[:1] == ["inbox"]:
        return len(key) == 4 and claim_of(folder.name, key[1]).get("token") == key[3]
    if key[:1] == ["message"]:
        from .agents import message_file
        path = message_file(folder, key[1]) if len(key) == 2 and isinstance(key[1], str) else None
        return path is not None and path.is_file()
    if len(key) < 3 or not isinstance(key[1], int):
        return False
    repo, number, role = key[:3]
    lines = subscriptions(folder)
    matching = [line for line in lines if line.repo.lower() == repo and line.role_name == role
                and line.number in (None, number)]
    return any(not line.excluded for line in matching) and not any(line.excluded for line in matching)


def prepare(folder: Path, now: float) -> Optional[dict]:
    recover(folder)
    batches = pending(folder)
    if batches:
        batch = batches[0][1]
        if batch["state"] not in SENDABLE or batch.get("next_attempt_at", 0) > now:
            return None
        return batch
    items = [item for item in queued(folder) if eligible(folder, item)]
    chosen = []
    size = 0
    for item in items:
        length = len(item["line"].replace("\0", "\\0").encode("utf-8")) + 1
        if size + length > LIMIT:
            break
        chosen.append(item)
        size += length
    if not chosen and items and inbox_key(items[0]):
        # A typed answer can be longer than a batch. Alone in a batch of its own, it cannot hold back the queue.
        chosen = items[:1]
    if not chosen:
        if items != queued(folder):
            save_queue(folder, items)
        if items:
            write_json(folder / "codex-last.json", {"state": "blocked", "last_error": "event exceeds batch limit"})
        return None
    batch = {"batch_id": str(uuid.uuid4()), "items": chosen, "state": "prepared", "attempts": 0,
             "next_attempt_at": now, "last_error": ""}
    path = folder / "codex-pending" / f"{batch['batch_id']}.json"
    path.parent.mkdir(exist_ok=True)
    write_json(path, batch)
    keys = {item["key"] for item in chosen}
    save_queue(folder, [item for item in items if item["key"] not in keys])
    return batch


def message(session: str, batch: dict) -> str:
    return (f"PROJECTOR WATCH {batch['batch_id']}\nsession={session}\n"
            "Recheck current subscriptions, pull request state, and inbox items before acting.\n"
            + "\n".join(item["line"].replace("\0", "\\0") for item in batch["items"]))


def acknowledge(folder: Path, session: str, prompt: str, now: float) -> tuple[bool, list[str]]:
    if not isinstance(prompt, str):
        raise UsageError("Codex hook prompt must be text")
    found = MARKER.match(prompt)
    if found is None:
        return False, []
    batch_id = found[1]
    try:
        valid = str(uuid.UUID(batch_id)) == batch_id
    except ValueError:
        valid = False
    if not valid:
        return False, []
    receipt = folder / "codex-receipts" / f"{batch_id}.json"
    with locked(folder):
        if receipt.exists():
            return True, []
        path = folder / "codex-pending" / f"{batch_id}.json"
        batch = read_json(path)
        if batch is None or prompt != message(session, batch):
            return False, []
        lines = [item["line"] for item in batch["items"] if eligible(folder, item)]
        consume(folder, batch_id, now)
    return True, lines


def consume(folder: Path, batch_id: str, now: float) -> None:
    path = folder / "codex-pending" / f"{batch_id}.json"
    keys = [key for item in (read_json(path) or {}).get("items") or [] if (key := inbox_key(item))]
    # Record the keys as waiting to settle before the receipt and the unlink, so a stop anywhere after this
    # leaves them where the next tick renews their claims and settles them, rather than nowhere.
    waiting = unsettled(folder)
    if any(key not in waiting for key in keys):
        write_json(folder / "codex-unsettled.json",
                   {"schema_version": 1, "keys": waiting + [key for key in keys if key not in waiting]})
    receipt = folder / "codex-receipts" / f"{batch_id}.json"
    receipt.parent.mkdir(exist_ok=True)
    write_json(receipt, {"batch_id": batch_id, "consumed_at": now})
    # A command result can race this. The receipt wins over that result.
    path.unlink(missing_ok=True)
    write_json(folder / "codex-last.json", {"batch_id": batch_id, "state": "consumed", "last_error": ""})
    settle(folder, keys)


def delivered(folder: Path, path: Path, batch: dict) -> bool:
    """Whether the thread's transcript shows that Codex started a turn with this batch, for a batch whose
    `UserPromptSubmit` acknowledgment never arrived. Scan only what the transcript gained since the last
    look, which began when the batch was submitted, so earlier text cannot match."""
    transcript = registration(folder).get("transcript_path")
    if not transcript:
        return False
    needle = json.dumps(f"PROJECTOR WATCH {batch['batch_id']}\nsession={folder.name}\n")[1:-1].encode()
    start = batch.get("transcript_offset", 0)
    try:
        with open(transcript, "rb") as stream:
            stream.seek(start)
            text = stream.read()
    except OSError:
        return False
    if needle in text:
        return True
    # Rescan a partial needle at the end, which the next write may complete.
    batch["transcript_offset"] = max(start, start + len(text) - len(needle) + 1)
    write_json(path, batch)
    return False


def disconnect(folder: Path) -> None:
    if not folder.is_dir():
        return
    with locked(folder):
        record = registration(folder)
        if record:
            write_json(folder / "codex.json", {**record, "enabled": False})
        (folder / "session.json").unlink(missing_ok=True)


def retry(folder: Path, now: float) -> None:
    with locked(folder):
        if sending(folder):
            raise UsageError("this batch is being submitted; wait for its result")
        recover(folder)
        batches = pending(folder)
        if not batches:
            raise UsageError("this session has no pending Codex batch")
        path, batch = batches[0]
        if batch["state"] == "submitting":
            raise UsageError("this batch is being submitted; wait for its result")
        batch.update(state="prepared", next_attempt_at=now, last_error="explicit retry can duplicate a message")
        write_json(path, batch)


def drain(folder: Path) -> None:
    from .watch import write_text
    recover(folder)
    items = queued(folder)
    # The plain events file has no reader that settles an inbox claim, so an inbox line stays out of it. Its
    # claim lapses once nothing renews it, and the session's next deliverer sends the line.
    lines = [item["line"] for item in items if not inbox_key(item) and eligible(folder, item)]
    removable = []
    for path, batch in pending(folder):
        if batch["state"] in ("prepared", "failed"):
            lines.extend(item["line"] for item in batch["items"] if not inbox_key(item) and eligible(folder, item))
            removable.append(path)
    if lines:
        events = folder / "events"
        previous = events.read_text(encoding="utf-8") if events.exists() else ""
        write_text(events, previous + "".join(line + "\n" for line in lines))
    if items:
        save_queue(folder, [])
    for path in removable:
        path.unlink()


def report(folder: Path, now: float) -> dict:
    record = registration(folder)
    if not record:
        return {}
    last = (pending(folder) or [(None, read_json(folder / "codex-last.json") or {})])[0][1]
    state = ("disconnected" if not record.get("enabled") or gone(record) else
             "expired" if not active(folder, now) else last.get("state", "idle"))
    error = ("Codex archived or deleted this conversation; resume it to reconnect" if gone(record) else
             last.get("last_error") or record.get("last_error", ""))
    return {"transport": "codex", "expires_at": record.get("expires_at"), "state": state,
            "unloaded_at": record.get("unloaded_at"), "batch_id": last.get("batch_id"), "last_error": error}


def register(folder: Path, session: str, cwd: str, now: float, *, transcript: Optional[str] = None,
             run: Callable = subprocess.run) -> dict:
    from .watch import LAPSE, seconds_of
    if not Path(cwd).is_absolute() or "\0" in cwd or not Path(cwd).is_dir():
        raise UsageError("Codex registration needs an existing absolute directory")
    with locked(folder):
        other = read_json(folder / "session.json") or {}
        renewed = seconds_of(other.get("renewed_at"))
        if renewed is not None and now - renewed < LAPSE:
            raise UsageError("this session already uses mod or stream delivery; disconnect it before switching")
        old = registration(folder)
        executable = shutil.which("codex")
        if executable is None or Path(executable).suffix.lower() in (".cmd", ".bat"):
            raise UsageError("Codex delivery needs a native codex executable with queue --resume")
        executable = str(Path(executable).absolute())
        stat = Path(executable).stat()
        signature = [executable, stat.st_mtime_ns, stat.st_size]
        supported = old.get("supported")
        if old.get("executable_signature") != signature:
            result = run([executable, "queue", "--help"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=5)
            supported = (result.returncode == 0 and all(option in result.stdout
                         for option in ("--thread", "--message", "--resume")))
        record = {"schema_version": 1, "thread_id": session, "executable": executable,
                  "executable_signature": signature, "cwd": cwd, "expires_at": now + LEASE,
                  "supported": supported, "enabled": bool(supported),
                  "last_error": "" if supported else "Codex CLI lacks queue --resume"}
        if transcript or old.get("transcript_path"):
            record["transcript_path"] = transcript or old["transcript_path"]
        write_json(folder / "codex.json", record)
        if not supported:
            raise UsageError("this Codex CLI lacks queue --resume; automatic delivery is unavailable")
        return record


HOOK_COMMAND = "project watch codex-hook"
HOOK_EVENTS = {"sessionStart", "sessionEnd", "userPromptSubmit", "postToolUse", "stop"}


def hook_problem(executable: str, cwd: str, *, popen: Callable = subprocess.Popen,
                 timeout: float = 10.0) -> Optional[str]:
    """What keeps Codex from running Projector's hooks, by Codex's own `hooks/list`, or None when it runs
    all five or cannot say. Codex re-gates a plugin's hooks whenever any file in the plugin changes."""
    hooks = listed_hooks(executable, cwd, popen=popen, timeout=timeout)
    if hooks is None:
        return None
    ours = [hook for hook in hooks if hook.get("command") == HOOK_COMMAND]
    running = {hook.get("eventName") for hook in ours
               if hook.get("enabled") and hook.get("trustStatus") in ("trusted", "managed")}
    if running >= HOOK_EVENTS:
        return None
    if not ours:
        return ("Codex has no Projector hooks; run `codex plugin add projector-hooks@projector`, "
                "then trust its hooks in /hooks")
    states = sorted({str(hook.get("trustStatus")) if hook.get("enabled") else "disabled"
                     for hook in ours if hook.get("eventName") not in running})
    return f"Codex is not running Projector's hooks ({', '.join(states)}); trust them in /hooks"


def listed_hooks(executable: str, cwd: str, *, popen: Callable, timeout: float) -> Optional[list[dict]]:
    """Every hook `codex app-server` lists for `cwd`, over its stdio JSON-RPC. The server exits at the end
    of its input before it answers, so stdin stays open until the answer arrives."""
    try:
        process = popen([executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    except OSError:
        return None
    lines: queue.Queue = queue.Queue()

    def read() -> None:
        with process.stdout:
            for line in process.stdout:
                lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + timeout

    def send(message: dict) -> None:
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()

    def call(number: int, method: str, params: dict) -> object:
        send({"id": number, "method": method, "params": params})
        while True:
            line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            if line is None:
                raise EOFError
            answer = json.loads(line)
            if isinstance(answer, dict) and answer.get("id") == number:
                return answer["result"]

    try:
        call(1, "initialize", {"clientInfo": {"name": "projector", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        send({"method": "initialized"})
        result = call(2, "hooks/list", {"cwds": [cwd]})
        return [hook for entry in result["data"] for hook in entry["hooks"] if isinstance(hook, dict)]
    except (OSError, ValueError, EOFError, queue.Empty, KeyError, TypeError):
        return None
    finally:
        process.kill()
        process.wait()
        with contextlib.suppress(OSError):
            process.stdin.close()


def tool_output(event: dict) -> Optional[str]:
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not push_command(command):
        return None
    response = event.get("tool_response")
    if event.get("tool_name") != "Bash":
        return None
    if isinstance(response, str):
        return response
    if not isinstance(response, dict) or not isinstance(response.get("exit_code"), int):
        return None
    return "\n".join(response[key] for key in ("output", "stdout", "stderr") if isinstance(response.get(key), str))


def push_command(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False
    groups: list[list[str]] = [[]]
    for word in words:
        if word and all(character in ";&|()\n" for character in word):
            groups.append([])
        else:
            groups[-1].append(word)
    for group in groups:
        while group and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", group[0])
                         or group[0] in {"command", "env", "exec", "nohup"}):
            group.pop(0)
        if not group:
            continue
        name, rest = Path(group[0]).name, group[1:]
        if name == "gh" and (rest[:1] == ["stack"] or rest[:2] == ["pr", "create"]):
            return True
        if name == "git":
            while rest:
                value = rest.pop(0)
                if value in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
                    rest = rest[1:]
                elif not value.startswith("-"):
                    if value == "push":
                        return True
                    break
    return False


def hook(root: Path, event: dict, now: float, ensure: Callable[[], object], *, run: Callable = subprocess.run) -> dict:
    from . import agents, watch
    from .config import load as load_config
    name, session, cwd = event.get("hook_event_name"), event.get("session_id"), event.get("cwd")
    if not isinstance(name, str) or name not in {"SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "Stop"}:
        raise UsageError("unsupported Codex hook event")
    if not isinstance(session, str) or not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise UsageError("Codex hook needs a session_id and absolute cwd")
    if not watch.SESSION.fullmatch(session):
        raise UsageError("Codex session_id contains invalid characters")
    if name == "UserPromptSubmit" and not isinstance(event.get("prompt"), str):
        raise UsageError("Codex hook prompt must be text")
    folder = watch.session_folder(root, session)
    transcript = own_transcript(session, event.get("transcript_path"))
    record = registration(folder)
    if name == "SessionStart":
        record = register(folder, session, cwd, now, transcript=transcript, run=run)
    elif not record:
        return {} if name == "SessionEnd" else {
            "systemMessage": "Projector: no Codex registration; start or resume a session with trusted hooks."}
    else:
        record = note(folder, now, transcript, unloaded=name == "SessionEnd")
    if name == "SessionEnd":
        # An end that archived the conversation lapses a registered agent, and only a running daemon
        # deletes its relay refs.
        if (folder / "agent.json").exists():
            ensure()
        return {}
    text = tool_output(event) if name == "PostToolUse" else None
    if text is not None:
        config = load_config(Path(cwd))
        if config.setting("watch.auto_start") is not False and watch.add_from_output(root, session, text)[2]:
            agents.register_subscriber(root, session, Path(cwd), now, config)
    consumed, lines = acknowledge(folder, session, event.get("prompt", ""), now) if name == "UserPromptSubmit" else (False, [])
    if record.get("enabled") and has_work(folder):
        ensure()
    if consumed and not lines:
        return {"decision": "block", "reason": "This watch batch is already consumed or its work was removed."}
    if name != "SessionStart" and not consumed:
        return {}
    context = (f"Use --session {session} for project watch and project inbox commands and pass this parent ID "
               "to workers. "
               + ("Codex watch delivery handles this session, its inbox answers included; do not start a stream "
                  "or project inbox watch. " if record.get("enabled") else
                  "Automatic Codex delivery is disconnected; inspect watch status before starting a stream. ") +
               "Recheck subscriptions, current pull request state, and inbox items before acting on events.")
    if consumed:
        context += " Process only these still-current events:\n" + "\n".join(lines)
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}


def note(folder: Path, now: float, transcript: Optional[str], *, unloaded: bool) -> dict:
    """Record a hook's host activity. Codex sends `SessionEnd` when it unloads a thread that sat idle, as
    well as on archive, delete, and exit, with no reason that tells them apart. So an end only marks the
    thread unloaded: delivery stays enabled, `queue --resume` loads the thread again, and a missing
    transcript shows that the conversation is gone."""
    with locked(folder):
        record = registration(folder)
        if record.get("enabled") and not unloaded:
            record["expires_at"] = now + LEASE
        if transcript:
            record["transcript_path"] = transcript
        record.pop("unloaded_at", None)
        if unloaded:
            record["unloaded_at"] = now
        write_json(folder / "codex.json", record)
        return record


class Dispatcher:
    def __init__(self, *, run: Callable = subprocess.run, wall: Callable[[], float] = time.time) -> None:
        self.run = run
        self.wall = wall
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="codex-watch")
        self.jobs: dict[Path, Future] = {}
        self.cleaned: dict[Path, float] = {}
        self.confirmed: dict[Path, float] = {}

    def tick(self, folders: list[Path]) -> None:
        self.jobs = {folder: job for folder, job in self.jobs.items() if not job.done()}
        folders = [folder for folder in folders if active(folder, self.wall())]
        # Renewal skips no folder, not even one whose batch is being sent, so a claim outlives the send.
        for folder in folders:
            renew(folder)
        for folder in folders:
            if len(self.jobs) >= 4:
                break
            if folder in self.jobs:
                continue
            with locked(folder):
                if self.wall() - self.cleaned.get(folder, float("-inf")) >= 3600:
                    prune_receipts(folder, self.wall())
                    self.cleaned[folder] = self.wall()
                if unsettled(folder):
                    settle(folder, unsettled(folder))
                offer(folder)
                batches = pending(folder)
                waiting = batches[0][1] if batches else {}
                if (waiting.get("state") in ("queued", "uncertain") and not self.confirm(folder, *batches[0])
                        and not (waiting["state"] == "uncertain" and waiting.get("next_attempt_at", 0) <= self.wall())):
                    continue
                batch = prepare(folder, self.wall())
            if batch is not None:
                self.jobs[folder] = self.pool.submit(self.send, folder, batch["batch_id"])

    def confirm(self, folder: Path, path: Path, batch: dict) -> bool:
        """Consume an accepted batch whose acknowledgment was lost, so it stops holding back every later one."""
        if self.wall() - self.confirmed.get(folder, float("-inf")) < CONFIRM_EVERY:
            return False
        self.confirmed[folder] = self.wall()
        if not delivered(folder, path, batch):
            return False
        consume(folder, batch["batch_id"], self.wall())
        return True

    def send(self, folder: Path, batch_id: str) -> None:
        with submission(folder) as held:
            if held:
                self._send(folder, batch_id)

    def _send(self, folder: Path, batch_id: str) -> None:
        path = folder / "codex-pending" / f"{batch_id}.json"
        with locked(folder):
            record = registration(folder)
            batch = read_json(path)
            if not active(folder, self.wall()) or not batch or batch["state"] not in SENDABLE:
                return
            batch["items"] = [item for item in batch["items"] if eligible(folder, item)]
            if not batch["items"]:
                path.unlink()
                return
            transcript = record.get("transcript_path")
            if not Path(record["cwd"]).is_dir() or not Path(record["executable"]).is_file():
                batch.update(state="failed", next_attempt_at=self.wall() + 300,
                             last_error="registered Codex executable or directory is missing")
                write_json(path, batch)
                return
            batch.update(state="submitting", attempts=batch["attempts"] + 1,
                         transcript_offset=Path(transcript).stat().st_size if transcript else 0,
                         submitted_at=self.wall())
            write_json(path, batch)
        try:
            result = self.run([record["executable"], "queue", "--thread", record["thread_id"],
                               "--message", message(folder.name, batch), "--resume"],
                              cwd=record["cwd"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT)
            state = "queued" if result.returncode == 0 else "failed"
            error = ("queued with a Codex warning; inspect the thread or resume it manually" if result.stderr else "") \
                if state == "queued" else "Codex queue failed; retry scheduled"
        except subprocess.TimeoutExpired:
            state, error = "uncertain", UNCERTAIN
        except OSError:
            state, error = "failed", "Codex could not start; retry scheduled"
        with locked(folder):
            if (folder / "codex-receipts" / path.name).exists() or not path.exists():
                return
            batch.update(state=state, last_error=error,
                         next_attempt_at=batch["submitted_at"] + RESEND if state == "uncertain" else
                         self.wall() + (30, 60, 120, 300)[min(batch["attempts"] - 1, 3)])
            write_json(path, batch)

    def close(self) -> None:
        # Waiting here would keep a stopping daemon's lock for up to TIMEOUT, past the 10 seconds a replacement
        # waits. A running call keeps the submission lock, so the next daemon leaves its batch alone, and the
        # interpreter joins the call's thread before this process exits, so its result is still written.
        self.pool.shutdown(wait=False, cancel_futures=True)
