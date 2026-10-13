"""The items an agent puts in the person's inbox, kept per session under Projector's state directory.

An item is a question, which the person answers or dismisses and whose answer goes back to the
agent, or a notice, which the person reads and dismisses and which sends nothing back. The agent
that posted an open item can withdraw it, which dismisses it and sends nothing back. Each
session numbers its items 1, 2, 3, and on, in one count across both kinds. Its items are JSON
files in their own directory, one per item and named by its number, with `count.json` holding the
last number given. Every write happens under the directory's lock, an operating-system lock on
its `.lock` file, and lands by renaming a temporary file into place, so two writers never lose
each other's change and a reader never sees half a file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterator, Optional
from urllib.parse import quote

from .core import ProjectorError

MAX_SUBJECT = 120
MAX_OPTIONS = 5
SNOOZE_MINUTES = 60
MAX_SNOOZE_MINUTES = 24 * 60
KINDS = ("question", "notice")
KEEP_DELIVERED = 100
CLAIM_EXPIRES_MS = 60_000
LOCK_WAIT_SECONDS = 5.0

SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PULL = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>[1-9][0-9]*)$")
NUMBER = re.compile(r"^[1-9][0-9]*$")
REFERENCE = re.compile(r"^(?P<ref>[1-9][0-9]*)(?:\.(?P<letter>[a-z]))?$")


class InboxError(ProjectorError):
    """An item fails a check, or an answer, claim, or snooze is not allowed in its state."""


class InboxBusy(InboxError):
    """Another command held the session's lock for the whole wait, so running the command again can work."""

    exit_code = 75


class ItemNotFound(ProjectorError):
    exit_code = 66


class AmbiguousItem(ProjectorError):
    exit_code = 67


def now_ms() -> int:
    return int(time.time() * 1000)


def store_root(environ: Optional[dict] = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "projector" / "inbox"


def session_dir(session: str) -> Path:
    if not SESSION.match(session):
        raise InboxError(f"{session!r} is not a session id: use letters, digits, `.`, `_`, and `-`")
    return store_root() / session


def item_file(folder: Path, ref: str) -> Path:
    return folder / f"{quote(ref, safe='')}.json"


if os.name == "nt":
    import msvcrt

    # `msvcrt.locking` locks bytes from the file's current position, so both calls seek to
    # byte 0 first and lock and unlock the same byte.
    def try_lock(descriptor: int) -> bool:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def unlock(descriptor: int) -> None:
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def try_lock(descriptor: int) -> bool:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def unlock(descriptor: int) -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextlib.contextmanager
def locked(folder: Path) -> Iterator[None]:
    """Hold the session's lock: the operating system's exclusive lock on its `.lock` file, which
    the system releases when the holder exits, so a command that dies holding it strands nothing."""
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / ".lock"
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while not try_lock(descriptor):
            if time.monotonic() > deadline:
                raise InboxBusy(f"another command holds {lock}; try again once it finishes")
            time.sleep(0.05)
        try:
            yield
        finally:
            unlock(descriptor)
    finally:
        os.close(descriptor)


def write_json(path: Path, value: object) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def item_in(path: Path) -> Optional[dict]:
    """The item a file holds, or None for a file that does not parse, whose reference is not a whole
    number, whose name does not match its reference, or whose kind is neither `question` nor
    `notice`, so such a file never breaks a command. An item stored under any other form of
    reference, such as `#337.3` or `q2`, is therefore never read, listed, delivered, or pruned."""
    item = read_json(path)
    if (item and isinstance(item.get("ref"), str) and NUMBER.match(item["ref"])
            and item_file(path.parent, item["ref"]) == path and item.get("kind") in KINDS):
        return item
    return None


def read_item(folder: Path, ref: str) -> dict:
    item = item_in(item_file(folder, ref))
    if item is None:
        raise ItemNotFound(f"{item_file(folder, ref)} is not an inbox item")
    return item


def load_items(folder: Path) -> list[dict]:
    """The session's items, oldest first, leaving out each file that holds none."""
    items = [item for item in map(item_in, folder.glob("*.json")) if item] if folder.is_dir() else []
    return sorted(items, key=lambda item: (item.get("postedAt", 0), item["ref"]))


def sessions() -> list[str]:
    root = store_root()
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and SESSION.match(path.name))


# Asking

def is_one_line(text: str) -> bool:
    """Whether text keeps to one line, as `watch` prints an answer and the band reads one."""
    return "\n" not in text and "\r" not in text


def check_subject(kind: str, subject: str, pr: Optional[str]) -> None:
    """The checks every item passes: a subject of one line with no tab and at most `MAX_SUBJECT`
    characters, and a pull request written as owner/repo#number."""
    if not subject.strip():
        raise InboxError(f"the {kind} is empty")
    if not is_one_line(subject):
        raise InboxError(f"the {kind} must be one line; put the rest in --detail")
    if "\t" in subject:
        raise InboxError(f"the {kind} holds a tab, which separates the columns `project inbox list` prints")
    if len(subject) > MAX_SUBJECT:
        raise InboxError(f"the {kind} is {len(subject)} characters; keep it to {MAX_SUBJECT} and put the "
                         "rest in --detail")
    if pr is not None and not PULL.match(pr):
        raise InboxError(f"{pr!r} is not a pull request; write it as owner/repo#number")


def check(question: str, recommend: str, options: list[str], pr: Optional[str]) -> None:
    check_subject("question", question, pr)
    if not recommend.strip():
        raise InboxError("give the answer you recommend with --recommend")
    if not is_one_line(recommend):
        raise InboxError("the recommendation must be one line; put the rest in --detail")
    if len(options) > MAX_OPTIONS:
        raise InboxError(f"{len(options)} other options; offer at most {MAX_OPTIONS} besides the recommendation")
    for index, option in enumerate(options):
        if not option.strip():
            raise InboxError("an --option is empty")
        if not is_one_line(option):
            raise InboxError(f"the option {option!r} must be one line; put the rest in --detail")
        if option.strip() == recommend.strip():
            raise InboxError(f"the option {option!r} repeats the recommendation, which is already answer a")
        if option.strip() in (other.strip() for other in options[:index]):
            raise InboxError(f"the option {option.strip()!r} is offered twice")


def post(session: str, kind: str, subject: str, *, detail: str, pr: Optional[str], agent: Optional[str],
         choices: Optional[list[str]] = None) -> dict:
    """Number and record an item that passed its checks; returns it. Questions and notices share
    the session's one count, which only grows, so a number names one item for the whole session,
    even after pruning deletes the item that held the largest."""
    folder = session_dir(session)
    with locked(folder):
        count_path = folder / "count.json"
        number = int((read_json(count_path) or {}).get("last", 0)) + 1
        record = {
            "ref": str(number),
            "kind": kind,
            "number": number,
            "pr": pr,
            "postedAt": now_ms(),
            "subject": subject.strip(),
            "detail": detail.strip(),
        }
        if choices is not None:
            record["choices"] = choices
        if agent:
            record["agentId"] = agent
        # The count lands first: a write that stops between the two then skips a
        # number, where the other order would hand the next item this number.
        write_json(count_path, {"last": number})
        write_json(item_file(folder, record["ref"]), record)
    return record


def ask(session: str, question: str, recommend: str, options: list[str], *, detail: str = "",
        pr: Optional[str] = None, agent: Optional[str] = None) -> dict:
    """Check, number, and record a question; returns it."""
    check(question, recommend, options, pr)
    choices = [recommend.strip(), *(option.strip() for option in options)]
    return post(session, "question", question, detail=detail, pr=pr, agent=agent, choices=choices)


def tell(session: str, notice: str, *, detail: str = "", pr: Optional[str] = None,
         agent: Optional[str] = None) -> dict:
    """Check, number, and record a notice; returns it."""
    check_subject("notice", notice, pr)
    return post(session, "notice", notice, detail=detail, pr=pr, agent=agent)


# Finding an item

def parse_reference(text: str) -> tuple[str, Optional[str]]:
    match = REFERENCE.match(text.strip())
    if not match:
        raise InboxError(f"{text!r} is not an inbox reference: name an item by its number, such as 3, "
                         "and an answer by the number and its letter, such as 3.a")
    return match["ref"], match["letter"]


def is_open(item: Optional[dict]) -> bool:
    return item is not None and not item.get("answer")


def locate(session: Optional[str], ref: str, prefer_open: bool = False) -> Path:
    """The folder holding `ref`: the session's, or, without one, the one session that has it. Every
    session counts from 1 and keeps its resolved items, so with `prefer_open` the sessions that hold
    `ref` open, with no answer recorded, are the candidates whenever any does."""
    if session is not None:
        folder = session_dir(session)
        if not item_file(folder, ref).is_file():
            raise ItemNotFound(f"session {session} has no item {ref}")
        return folder
    holders = [name for name in sessions() if item_file(store_root() / name, ref).is_file()]
    if prefer_open:
        holders = [name for name in holders if is_open(item_in(item_file(store_root() / name, ref)))] or holders
    if not holders:
        raise ItemNotFound(f"no session has an item {ref}")
    if len(holders) > 1:
        raise AmbiguousItem(f"{ref} names an item in {len(holders)} sessions: {', '.join(holders)}; "
                            "pass --session with the one `project inbox list` prints beside the item")
    return store_root() / holders[0]


def find(session: Optional[str], ref: str) -> dict:
    return read_item(locate(session, ref, prefer_open=True), ref)


def is_notice(item: dict) -> bool:
    return item.get("kind") == "notice"


def refuse_notice(item: dict, action: str) -> None:
    if is_notice(item):
        raise InboxError(f"{item['ref']} is a notice, which {action}; dismiss it with `project inbox dismiss`")


def typed_letter(question: dict) -> str:
    return chr(ord("a") + len(question["choices"]))


def answer_ref(item: dict) -> str:
    answered = item.get("answer") or {}
    return item["ref"] if answered.get("dismissed") else f"{item['ref']}.{answered['letter']}"


def resolution(item: dict) -> str:
    """How a resolved item was resolved, as a refusal names it: `withdrawn`, `dismissed`, or
    `answered, as 3.a`."""
    answered = item["answer"]
    if answered.get("withdrawn"):
        return "withdrawn"
    return "dismissed" if answered.get("dismissed") else f"answered, as {answer_ref(item)}"


def message(question: dict) -> str:
    """The line that delivers an answer, as `watch` prints it and the band sends it."""
    answered = question["answer"]
    if answered.get("dismissed"):
        return f"DISMISSED {question['ref']} {question['subject']}"
    return f"ANSWER {answer_ref(question)} {question['subject']} → {answered['text']}"


# Answering and delivering

def update(session: Optional[str], ref: str, change, prefer_open: bool = False) -> dict:
    """Read the item under the lock, let `change` alter it, and write it back."""
    folder = locate(session, ref, prefer_open)
    with locked(folder):
        item = read_item(folder, ref)
        change(item)
        write_json(item_file(folder, ref), item)
        if item.get("deliveredAt"):
            prune(folder)
    return item


def answer(session: Optional[str], ref: str, letter: Optional[str], text: Optional[str] = None) -> dict:
    def record(question: dict) -> None:
        refuse_notice(question, "takes no answer")
        if question.get("answer"):
            raise InboxError(f"{question['ref']} is already {resolution(question)}")
        last = typed_letter(question)
        if letter is None:
            raise InboxError(f"name the answer's letter, such as {question['ref']}.a")
        if letter == last:
            if not (text or "").strip():
                raise InboxError(f"{question['ref']}.{last} is a typed answer; give it with --text")
            if not is_one_line(text):
                raise InboxError("the typed answer must be one line")
            chosen = text.strip()
        elif text is not None:
            raise InboxError(f"--text goes with the typed answer, {question['ref']}.{last}")
        elif ord(letter) - ord("a") < len(question["choices"]):
            chosen = question["choices"][ord(letter) - ord("a")]
        else:
            raise InboxError(f"{question['ref']} has no answer {letter}; its letters run a to {last}")
        question["answer"] = {"letter": letter, "text": chosen, "answeredAt": now_ms()}
        question.pop("snoozedUntil", None)

    return update(session, ref, record, prefer_open=True)


def dismiss(session: Optional[str], ref: str, withdrawn: bool = False) -> dict:
    """Dismiss an item. A dismissed question waits for its deliverer to tell the agent; a dismissed
    notice has nothing to send, so it is delivered at once. A `withdrawn` item, which the agent that
    posted it took back, is delivered at once too, because that agent already knows."""
    def record(item: dict) -> None:
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}")
        now = now_ms()
        item["answer"] = {"dismissed": True, "answeredAt": now, **({"withdrawn": True} if withdrawn else {})}
        item.pop("snoozedUntil", None)
        if withdrawn or is_notice(item):
            item["deliveredAt"] = now

    return update(session, ref, record, prefer_open=True)


def snooze(session: Optional[str], ref: str, minutes: int = SNOOZE_MINUTES) -> dict:
    """Hide an open item until `minutes` from now, a day at most, or wake it now with 0."""
    if not 0 <= minutes <= MAX_SNOOZE_MINUTES:
        raise InboxError(f"snooze for 0 to {MAX_SNOOZE_MINUTES} minutes, a day at most, not {minutes}")

    def record(item: dict) -> None:
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}, so it has nothing to return for")
        item["snoozedUntil"] = now_ms() + minutes * 60_000

    return update(session, ref, record, prefer_open=True)


def snoozed_until(item: dict) -> Optional[int]:
    """When a snoozed item returns, while that time is still ahead."""
    until = item.get("snoozedUntil")
    return until if isinstance(until, int) and until > now_ms() else None


def live_claim(question: dict) -> Optional[dict]:
    claim = question.get("claim")
    if claim and now_ms() - int(claim.get("renewedAt", 0)) < CLAIM_EXPIRES_MS:
        return claim
    return None


def claim(session: Optional[str], ref: str, renew: Optional[str] = None) -> dict:
    """Claim an answer for delivery, or renew a claim by its token; returns the question."""
    def take(question: dict) -> None:
        refuse_notice(question, "has nothing to deliver")
        if not question.get("answer"):
            raise InboxError(f"{question['ref']} has no answer to deliver")
        if question.get("deliveredAt"):
            raise InboxError(f"{answer_ref(question)} is already delivered")
        if renew is not None:
            if (question.get("claim") or {}).get("token") != renew:
                raise InboxError(f"{answer_ref(question)} is not claimed with that token")
            question["claim"]["renewedAt"] = now_ms()
            return
        if live_claim(question):
            raise InboxError(f"{answer_ref(question)} is already claimed for delivery")
        question["claim"] = {"token": secrets.token_hex(8), "renewedAt": now_ms()}

    return update(session, ref, take)


def settle(session: Optional[str], ref: str, token: str, delivered: bool) -> dict:
    """Mark a claimed answer delivered, or reopen the question when its delivery failed."""
    def finish(question: dict) -> None:
        if question.get("deliveredAt"):
            raise InboxError(f"{answer_ref(question)} is already delivered")
        if (question.get("claim") or {}).get("token") != token:
            raise InboxError(f"{question['ref']} is not claimed with that token")
        del question["claim"]
        if delivered:
            question["deliveredAt"] = now_ms()
        else:
            del question["answer"]

    return update(session, ref, finish)


def prune(folder: Path) -> None:
    delivered = [item for item in load_items(folder) if item.get("deliveredAt")]
    delivered.sort(key=lambda item: item["deliveredAt"], reverse=True)
    for item in delivered[KEEP_DELIVERED:]:
        with contextlib.suppress(FileNotFoundError):
            item_file(folder, item["ref"]).unlink()


def undelivered(session: str) -> list[dict]:
    return [item for item in load_items(session_dir(session))
            if item.get("answer") and not item.get("deliveredAt") and not live_claim(item)]


def deliver_pass(session: str, send: Callable[[str], None]) -> None:
    """One pass of `watch`: claim each undelivered answer, `send` its line, and only then mark it
    delivered, so an answer whose line fails to send keeps just a claim, which lapses for the next
    deliverer."""
    for question in undelivered(session):
        try:
            claimed = claim(session, question["ref"])
        except InboxError:
            continue
        send(message(claimed))
        settle(session, claimed["ref"], claimed["claim"]["token"], delivered=True)
