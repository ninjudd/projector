"""The items an agent puts in the person's inbox, kept per session under Projector's state directory.

An item is a question, which the person answers or dismisses and whose answer goes back to the
agent; a notice, which the person reads and dismisses and which sends nothing back; or a request,
work only the person can do, which they mark done or decline and whose outcome goes back to the
agent. A request can carry a start action, what the agent does once the person presses start,
and the agent then marks it ready or blocked. The agent that posted an open item can withdraw it,
which dismisses it and sends nothing back, and can revise an open question's or request's text in
place. An item the person dismissed, declined, or snoozed can be restored, which reopens it in
place. Each session numbers its items 1, 2, 3, and on, in one count across every kind. Its items
are JSON files in their own directory, one per item and named by its number, with `count.json`
holding the last number given. Every write happens under the
directory's lock, an operating-system lock on its `.lock` file, and lands by renaming a temporary
file into place, so two writers never lose each other's change and a reader never sees half a
file.
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
KINDS = ("question", "notice", "request")
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
    number, whose name does not match its reference, or whose kind is not one of `KINDS`, so such a
    file never breaks a command. An item stored under any other form of
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
    """Whether text keeps to one line, as `watch` prints an answer and the panel reads one."""
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
         choices: Optional[list[str]] = None, start: Optional[str] = None) -> dict:
    """Number and record an item that passed its checks; returns it. Every kind shares the
    session's one count, which only grows, so a number names one item for the whole session,
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
        if start is not None:
            record["start"] = start.strip()
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


def check_start(action: str) -> None:
    """The checks a start action passes: those a subject passes, so it reads on one line of the
    drawer and of the `STARTED` line that quotes it."""
    check_subject("start action", action, None)


def check_request_options(options: list[str]) -> None:
    """The checks a request's options pass: at most `MAX_OPTIONS`, so they keep the digits a start
    leaves, each one line and offered once, and none `Declined`, which declines a request."""
    if len(options) > MAX_OPTIONS:
        raise InboxError(f"{len(options)} options; offer a request at most {MAX_OPTIONS}")
    for index, option in enumerate(options):
        if not option.strip():
            raise InboxError("an --option is empty")
        if not is_one_line(option):
            raise InboxError(f"the option {option!r} must be one line; put the rest in --detail")
        if option.strip() == "Declined":
            raise InboxError("`Declined` declines a request, so it cannot be one of its options")
        if option.strip() in (other.strip() for other in options[:index]):
            raise InboxError(f"the option {option.strip()!r} is offered twice")


def request(session: str, subject: str, *, detail: str = "", pr: Optional[str] = None,
            agent: Optional[str] = None, start: Optional[str] = None, options: Optional[list[str]] = None) -> dict:
    """Check, number, and record a request, whose detail holds its steps, whose `start`, when it
    has one, is what the agent does once the person presses start, and whose `options`, when it
    gives them, are what the person chooses between when they finish; returns it."""
    check_subject("request", subject, pr)
    if start is not None:
        check_start(start)
    check_request_options(options or [])
    choices = [option.strip() for option in options] if options else None
    return post(session, "request", subject, detail=detail, pr=pr, agent=agent, start=start, choices=choices)


# Finding an item

def parse_reference(text: str) -> tuple[str, Optional[str]]:
    match = REFERENCE.match(text.strip())
    if not match:
        raise InboxError(f"{text!r} is not an inbox reference: name an item by its number, such as 3, "
                         "and an answer by the number and its letter, such as 3.a")
    return match["ref"], match["letter"]


def is_open(item: Optional[dict]) -> bool:
    return item is not None and not item.get("answer")


def is_restorable(item: Optional[dict]) -> bool:
    """Whether `restore` can bring an item back: the person dismissed it, or it is snoozed."""
    if item is None:
        return False
    answered = item.get("answer")
    if answered:
        return bool(answered.get("dismissed")) and not answered.get("withdrawn")
    return snoozed_until(item) is not None


def locate(session: Optional[str], ref: str, prefer: Optional[Callable[[Optional[dict]], bool]] = None) -> Path:
    """The folder holding `ref`: the session's, or, without one, the one session that has it. Every
    session counts from 1 and keeps its resolved items, so with `prefer` the sessions whose item
    `ref` it accepts, such as the ones that hold it open, are the candidates whenever any is."""
    if session is not None:
        folder = session_dir(session)
        if not item_file(folder, ref).is_file():
            raise ItemNotFound(f"session {session} has no item {ref}")
        return folder
    holders = [name for name in sessions() if item_file(store_root() / name, ref).is_file()]
    if prefer is not None:
        holders = [name for name in holders if prefer(item_in(item_file(store_root() / name, ref)))] or holders
    if not holders:
        raise ItemNotFound(f"no session has an item {ref}")
    if len(holders) > 1:
        raise AmbiguousItem(f"{ref} names an item in {len(holders)} sessions: {', '.join(holders)}; "
                            "pass --session with the one `project inbox list` prints beside the item")
    return store_root() / holders[0]


def find(session: Optional[str], ref: str) -> dict:
    return read_item(locate(session, ref, is_open), ref)


def is_notice(item: dict) -> bool:
    return item.get("kind") == "notice"


def is_request(item: dict) -> bool:
    return item.get("kind") == "request"


def refuse_notice(item: dict, action: str) -> None:
    if is_notice(item):
        raise InboxError(f"{item['ref']} is a notice, which {action}; dismiss it with `project inbox dismiss`")


def refuse_request(item: dict) -> None:
    if is_request(item):
        raise InboxError(f"{item['ref']} is a request, which takes no answer; record its outcome with "
                         "`project inbox done` or `project inbox decline`")


def refuse_unless_request(item: dict, command: str) -> None:
    if not is_request(item):
        raise InboxError(f"{item['ref']} is a {item['kind']}, and only a request can be marked {command}")


def typed_letter(question: dict) -> str:
    return chr(ord("a") + len(question["choices"]))


def answer_ref(item: dict) -> str:
    letter = (item.get("answer") or {}).get("letter")
    return f"{item['ref']}.{letter}" if letter else item["ref"]


def resolution(item: dict) -> str:
    """How a resolved item was resolved, as a refusal names it: `withdrawn`, `declined`,
    `dismissed`, `done`, or `answered, as 3.a`."""
    answered = item["answer"]
    if answered.get("withdrawn"):
        return "withdrawn"
    if answered.get("declined"):
        return "declined"
    if answered.get("dismissed"):
        return "dismissed"
    return "done" if answered.get("done") else f"answered, as {answer_ref(item)}"


def owed_line(item: dict) -> Optional[str]:
    """The line an item owes its agent next, if any: `answer` for an answer, outcome, or dismissal
    not yet delivered, `restored` for word that a dismissal it may have heard of was undone, or
    `started` for an open request's start not yet delivered."""
    if item.get("answer") and not item.get("deliveredAt"):
        return "answer"
    if item.get("restoredAt"):
        return "restored"
    started = item.get("started")
    if not item.get("answer") and started and not started.get("deliveredAt"):
        return "started"
    return None


def claimed_line(item: dict) -> Optional[str]:
    """The line a claim on the item delivers: the one `claim` recorded, or, for a claim an earlier
    release made without recording it, the one the item owes."""
    claimed = item.get("claim")
    return (claimed.get("line") or owed_line(item)) if claimed else None


def message(question: dict) -> str:
    """The line that tells the agent what became of its question or request, as `watch` prints it
    and the panel sends it: its answer or outcome, its dismissal, that a dismissal it may have
    heard of was undone, or that the person pressed start, quoting the start action they pressed it
    for. A request's outcome ends with the person's text when they gave one."""
    line = claimed_line(question) or owed_line(question)
    if line == "started":
        return f"STARTED {question['ref']} {question['subject']} → {question['started']['action']}"
    if question.get("restoredAt"):
        return f"RESTORED {question['ref']} {question['subject']}"
    answered = question["answer"]
    if answered.get("declined") or answered.get("done"):
        word = "DECLINED" if answered.get("declined") else "DONE"
        said = f" → {answered['text']}" if answered.get("text") else ""
        return f"{word} {question['ref']} {question['subject']}{said}"
    if answered.get("dismissed"):
        return f"DISMISSED {question['ref']} {question['subject']}"
    return f"ANSWER {answer_ref(question)} {question['subject']} → {answered['text']}"


# Answering and delivering

def update(session: Optional[str], ref: str, change,
           prefer: Optional[Callable[[Optional[dict]], bool]] = None) -> dict:
    """Read the item under the lock, let `change` alter it, and write it back."""
    folder = locate(session, ref, prefer)
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
        refuse_request(question)
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
        # The answer tells the agent the question was open, so it replaces word of a restore that
        # has not reached the agent, and any claim to deliver that word.
        for key in ("snoozedUntil", "snoozedAt", "restoredAt", "claim"):
            question.pop(key, None)

    return update(session, ref, record, is_open)


def outcome_text(text: Optional[str]) -> dict:
    """The `text` a request's outcome records: the person's one line, or nothing."""
    if text is not None and not is_one_line(text):
        raise InboxError("the text must be one line")
    return {"text": text.strip()} if text and text.strip() else {}


def clear_for_outcome(item: dict) -> tuple[bool, bool]:
    """Clear what an answer, outcome, or dismissal replaces, and say whether word of a restore was
    owed and whether a claim was dropped. It clears a snooze, word of a restore, the claim to deliver
    either, and a request's start that no deliverer has claimed. A claimed `STARTED` may already wait
    in the session's queue as a prompt that nothing can recall, so it keeps its claim, and the new
    line is owed once `settle` finishes it."""
    starting = claimed_line(item) == "started"
    item.pop("snoozedUntil", None)
    item.pop("snoozedAt", None)
    restored = item.pop("restoredAt", None) is not None
    if starting:
        return restored, False
    claimed = item.pop("claim", None) is not None
    started = item.get("started")
    if started and not started.get("deliveredAt"):
        del item["started"]
    return restored, claimed


def record_dismissal(item: dict, withdrawn: bool, said: dict) -> None:
    if item.get("answer"):
        raise InboxError(f"{item['ref']} is already {resolution(item)}")
    now = now_ms()
    restored, claimed = clear_for_outcome(item)
    declined = {"declined": True, **said} if is_request(item) and not withdrawn else {}
    item["answer"] = {"dismissed": True, **declined, "answeredAt": now, **({"withdrawn": True} if withdrawn else {})}
    if withdrawn or is_notice(item) or (restored and not claimed):
        item["deliveredAt"] = now


def dismiss(session: Optional[str], ref: str, withdrawn: bool = False) -> dict:
    """Dismiss an item. A dismissed question or request waits for its deliverer to tell the agent; a
    dismissed notice has nothing to send, so it is delivered at once. Dismissing a request declines
    it, with no text. A `withdrawn` item, which the agent that posted it took back, is delivered at
    once too, because that agent already knows. So is an item dismissed again before any deliverer
    claimed the `RESTORED` line its restore owes, because the last line its agent heard said
    `DISMISSED` or `DECLINED`."""
    return update(session, ref, lambda item: record_dismissal(item, withdrawn, {}), is_open)


def decline(session: Optional[str], ref: str, text: Optional[str] = None) -> dict:
    """Decline a request, with the person's one line of text when they give one: a dismissal its
    deliverer reports as `DECLINED`."""
    said = outcome_text(text)

    def record(item: dict) -> None:
        refuse_unless_request(item, "declined")
        record_dismissal(item, False, said)

    return update(session, ref, record, is_open)


def done(session: Optional[str], ref: str, text: Optional[str] = None) -> dict:
    """Mark a request done, with the person's one line of text when they give one."""
    said = outcome_text(text)

    def record(item: dict) -> None:
        refuse_unless_request(item, "done")
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}")
        # As an answer does, the outcome replaces word of a restore that has not reached the agent.
        clear_for_outcome(item)
        item["answer"] = {"done": True, **said, "answeredAt": now_ms()}

    return update(session, ref, record, is_open)


def reset_start(item: dict) -> None:
    """Return a request to open, before any start: its start, the agent's progress, and the claim
    to deliver its `STARTED` go, so only a new press of start sends one."""
    if claimed_line(item) == "started":
        del item["claim"]
    item.pop("started", None)
    item.pop("progress", None)


def revise(session: Optional[str], ref: str, *, question: Optional[str] = None, detail: Optional[str] = None,
           recommend: Optional[str] = None, options: Optional[list[str]] = None,
           request: Optional[str] = None, start: Optional[str] = None) -> dict:
    """Replace an open question's or request's text in place, keeping its number, its place in the
    list, and its snooze. Each part left as None keeps its text; `options` replaces every option but
    the recommendation. A question takes `question`, `recommend`, and `options`, a request takes
    `request`, `start`, where an empty `start` removes it, and `options`, which replaces all its
    options, an empty list removing them, and both take `detail`. The revised item passes the
    checks its posting made. A revised request returns to open, so a press of start made for its old
    text never covers the new."""
    if all(part is None for part in (question, detail, recommend, options, request, start)):
        raise InboxError("give the new text with --question, --request, --start, --detail, --recommend, or "
                         "--option")

    def record(item: dict) -> None:
        if is_notice(item):
            raise InboxError(f"{item['ref']} is a notice, and only a question or a request can be revised; "
                             "withdraw it and tell again")
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}")
        if is_request(item):
            if question is not None or recommend is not None:
                raise InboxError(f"{item['ref']} is a request; revise its text with --request, --start, --option, "
                                 "and --detail")
            subject = item["subject"] if request is None else request
            check_subject("request", subject, item.get("pr"))
            if start:
                check_start(start)
            if options is not None:
                check_request_options(options)
                if options:
                    item["choices"] = [option.strip() for option in options]
                else:
                    item.pop("choices", None)
            item["subject"] = subject.strip()
            if detail is not None:
                item["detail"] = detail.strip()
            if start is not None and start.strip():
                item["start"] = start.strip()
            elif start is not None:
                item.pop("start", None)
            reset_start(item)
            return
        if request is not None or start is not None:
            raise InboxError(f"{item['ref']} is a question; revise its text with --question")
        subject = item["subject"] if question is None else question
        first, *others = item["choices"]
        chosen = first if recommend is None else recommend
        rest = others if options is None else options
        check(subject, chosen, rest, item.get("pr"))
        item["subject"] = subject.strip()
        item["choices"] = [chosen.strip(), *(option.strip() for option in rest)]
        if detail is not None:
            item["detail"] = detail.strip()

    return update(session, ref, record, is_open)


def snooze(session: Optional[str], ref: str, minutes: int = SNOOZE_MINUTES) -> dict:
    """Hide an open item until `minutes` from now, a day at most, or wake it now with 0."""
    if not 0 <= minutes <= MAX_SNOOZE_MINUTES:
        raise InboxError(f"snooze for 0 to {MAX_SNOOZE_MINUTES} minutes, a day at most, not {minutes}")

    def record(item: dict) -> None:
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}, so it has nothing to return for")
        now = now_ms()
        item["snoozedAt"] = now
        item["snoozedUntil"] = now + minutes * 60_000

    return update(session, ref, record, is_open)


def restore(session: Optional[str], ref: str) -> dict:
    """Bring back, in place and open, an item the person dismissed or snoozed. A snoozed item loses
    its snooze. A dismissed item loses its dismissal and what its delivery recorded, and keeps
    everything else. When the agent may have heard `DISMISSED`, because the line was delivered or
    claimed for delivery, the item records `restoredAt`, and its deliverer sends `RESTORED`;
    otherwise the restore cancels the line, and nothing goes out."""
    def record(item: dict) -> None:
        answered = item.get("answer")
        if not answered:
            if snoozed_until(item) is None:
                raise InboxError(f"{item['ref']} is open, so there is nothing to restore")
            item.pop("snoozedUntil")
            item.pop("snoozedAt", None)
            return
        if answered.get("withdrawn"):
            raise InboxError(f"{item['ref']} was withdrawn by the agent that posted it, so it is that agent's to "
                             "post again")
        if not answered.get("dismissed"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}, and only a dismissed or snoozed item "
                             "can be restored")
        if not is_notice(item) and (item.get("deliveredAt") or item.get("claim")):
            item["restoredAt"] = now_ms()
        for key in ("answer", "deliveredAt", "claim"):
            item.pop(key, None)
        # The agent heard the request declined and may have undone what it prepared, so a restored
        # request waits for a new press of start.
        reset_start(item)

    return update(session, ref, record, is_restorable)


def start(session: Optional[str], ref: str, action: str) -> dict:
    """Record that the person pressed start on a request, consenting to `action`, the start action
    their drawer showed, and nothing more. A drawer drawn before the agent revised the start action
    shows the old one, so a press whose action differs from the stored one records nothing. A
    blocked request takes a new press, which is a retry. The deliverer then sends `STARTED`."""
    def record(item: dict) -> None:
        refuse_unless_request(item, "started")
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}")
        stored = item.get("start")
        if stored is None:
            raise InboxError(f"{item['ref']} has no start action; mark it done or decline it")
        if action.strip() != stored:
            raise InboxError(f"{item['ref']}'s start action is now {stored!r}; read it and press start again")
        if item.get("started") and (item.get("progress") or {}).get("state") != "blocked":
            raise InboxError(f"{item['ref']} is already started")
        item["started"] = {"action": stored, "startedAt": now_ms()}
        item.pop("progress", None)

    return update(session, ref, record, is_open)


def report(session: Optional[str], ref: str, state: str, reason: Optional[str] = None) -> dict:
    """Record what the agent made of a request's start: `ready`, once it did the start action, or
    `blocked`, with the `reason` the person reads before they press retry. Nothing goes back to the
    agent, which wrote it."""
    if reason is not None:
        check_subject("reason", reason, None)

    def record(item: dict) -> None:
        refuse_unless_request(item, state)
        if item.get("answer"):
            raise InboxError(f"{item['ref']} is already {resolution(item)}")
        if not item.get("started"):
            raise InboxError(f"{item['ref']} is not started; wait for `STARTED {item['ref']}` before marking it "
                             f"{state}")
        if item.get("progress"):
            raise InboxError(f"{item['ref']} is already {item['progress']['state']}")
        item["progress"] = {"state": state, **({"reason": reason.strip()} if reason else {}), "at": now_ms()}

    return update(session, ref, record, is_open)


def ready(session: Optional[str], ref: str) -> dict:
    return report(session, ref, "ready")


def block(session: Optional[str], ref: str, reason: str) -> dict:
    return report(session, ref, "blocked", reason)


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
    """Claim the line an item owes for delivery, recording which line it is, or renew a claim by its
    token; returns the item. A claimed `STARTED` renews even once the request is resolved, since
    its line still waits in the session's queue."""
    def take(question: dict) -> None:
        refuse_notice(question, "has nothing to deliver")
        line = owed_line(question)
        if line is None and not (renew is not None and claimed_line(question) == "started"):
            if question.get("deliveredAt"):
                raise InboxError(f"{answer_ref(question)} is already delivered")
            raise InboxError(f"{question['ref']} has no answer to deliver")
        if renew is not None:
            if (question.get("claim") or {}).get("token") != renew:
                raise InboxError(f"{answer_ref(question)} is not claimed with that token")
            question["claim"]["renewedAt"] = now_ms()
            return
        if live_claim(question):
            raise InboxError(f"{answer_ref(question)} is already claimed for delivery")
        question["claim"] = {"token": secrets.token_hex(8), "renewedAt": now_ms(), "line": line}

    return update(session, ref, take)


def settle(session: Optional[str], ref: str, token: str, delivered: bool) -> dict:
    """Mark a claimed line delivered, or undo what it reported when its delivery failed: a question
    reopens, and a request's start is removed, so the person presses start again. A claimed
    `RESTORED` line is done either way: the panel does not retry it, and the item's answer or next
    dismissal still reaches the agent. An outcome recorded while a `STARTED` was claimed is owed
    once that line settles."""
    def finish(question: dict) -> None:
        line = claimed_line(question)
        if line != "started" and question.get("deliveredAt"):
            raise InboxError(f"{answer_ref(question)} is already delivered")
        if (question.get("claim") or {}).get("token") != token:
            raise InboxError(f"{question['ref']} is not claimed with that token")
        del question["claim"]
        if line == "started":
            if delivered:
                question["started"]["deliveredAt"] = now_ms()
            else:
                question.pop("started")
            return
        if question.pop("restoredAt", None) is not None:
            return
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


def owes_a_line(item: dict) -> bool:
    """Whether the item's agent is owed a line: an answer, outcome, or dismissal not yet delivered,
    word that a dismissal it may have heard of was undone, or a request's start."""
    return owed_line(item) is not None


def undelivered(session: str) -> list[dict]:
    return [item for item in load_items(session_dir(session)) if owes_a_line(item) and not live_claim(item)]


def deliver_pass(session: str, send: Callable[[str], None]) -> None:
    """One pass of `watch`: claim each undelivered answer, `send` its line, and only then mark it
    delivered, so an answer whose line fails to send keeps just a claim, which lapses for the next
    deliverer. A restore or an answer that lands while the line goes out drops the claim, so the
    line cannot be marked delivered, and the next pass sends what the item owes then."""
    for question in undelivered(session):
        try:
            claimed = claim(session, question["ref"])
        except InboxError:
            continue
        send(message(claimed))
        try:
            settle(session, claimed["ref"], claimed["claim"]["token"], delivered=True)
        except InboxBusy:
            raise
        except InboxError:
            continue
