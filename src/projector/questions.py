"""Questions an agent asks the person, kept per session under Projector's state directory.

Each session's questions are JSON files in their own directory, one per question, with
`counts.json` holding the last number given in each count and `session.json` the session's
repository, once an `ask` from a checkout has recorded one. Every write happens under the
directory's lock, an operating-system lock on its `.lock` file, and lands by renaming a
temporary file into place, so two writers never lose each other's change and a reader never
sees half a file.
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

MAX_QUESTION = 120
MAX_OPTIONS = 5
KEEP_DELIVERED = 100
CLAIM_EXPIRES_MS = 60_000
LOCK_WAIT_SECONDS = 5.0

SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PULL = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>[1-9][0-9]*)$")
REFERENCE = re.compile(
    r"^(?P<ref>(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[1-9][0-9]*\.[1-9][0-9]*|q[1-9][0-9]*)"
    r"(?:\.(?P<letter>[a-z]))?$"
)
# A reference in prose stands alone: nothing it could be part of on its left, and on its
# right a space, punctuation other than `.`, or a `.` that ends a sentence.
IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9/#.])"
    r"(?P<ref>(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[1-9][0-9]*\.[1-9][0-9]*|q[1-9][0-9]*)"
    r"(?:\.(?P<letter>[a-z]))?"
    r"(?=$|\s|[^\w./#]|\.(?:\s|$))"
)


class QuestionError(ProjectorError):
    """A question fails a check, or an answer or claim is not allowed in its state."""


class QuestionNotFound(ProjectorError):
    exit_code = 66


class AmbiguousQuestion(ProjectorError):
    exit_code = 67


def now_ms() -> int:
    return int(time.time() * 1000)


def store_root(environ: Optional[dict] = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "projector" / "questions"


def session_dir(session: str) -> Path:
    if not SESSION.match(session):
        raise QuestionError(f"{session!r} is not a session id: use letters, digits, `.`, `_`, and `-`")
    return store_root() / session


def question_file(folder: Path, ref: str) -> Path:
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
                raise QuestionError(f"another command holds {lock}; try again once it finishes")
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


def load_questions(folder: Path) -> list[dict]:
    """The session's questions, oldest first; a file that does not parse, or whose name does
    not match its reference, is skipped rather than allowed to break a view."""
    questions = []
    if not folder.is_dir():
        return questions
    for path in folder.glob("*.json"):
        question = read_json(path)
        if question and isinstance(question.get("ref"), str) and question_file(folder, question["ref"]) == path:
            questions.append(question)
    return sorted(questions, key=lambda question: (question.get("askedAt", 0), question["ref"]))


def sessions() -> list[str]:
    root = store_root()
    return sorted(path.name for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


# Asking

def reference(pull: Optional[re.Match], count: int, repo: Optional[str]) -> str:
    if pull is None:
        return f"q{count}"
    if repo and pull["repo"].lower() == repo.lower():
        return f"#{pull['number']}.{count}"
    return f"{pull['repo']}#{pull['number']}.{count}"


def check(question: str, recommend: str, options: list[str], pr: Optional[str]) -> None:
    if not question.strip():
        raise QuestionError("the question is empty")
    if "\n" in question or "\r" in question:
        raise QuestionError("the question must be one line; put the rest in --detail")
    if len(question) > MAX_QUESTION:
        raise QuestionError(f"the question is {len(question)} characters; keep it to {MAX_QUESTION} and put the "
                            "rest in --detail")
    if not recommend.strip():
        raise QuestionError("give the answer you recommend with --recommend")
    if len(options) > MAX_OPTIONS:
        raise QuestionError(f"{len(options)} other options; offer at most {MAX_OPTIONS} besides the recommendation")
    for option in options:
        if not option.strip():
            raise QuestionError("an --option is empty")
        if option.strip() == recommend.strip():
            raise QuestionError(f"the option {option!r} repeats the recommendation, which is already answer a")
    if pr is not None and not PULL.match(pr):
        raise QuestionError(f"{pr!r} is not a pull request; write it as owner/repo#number")


def session_repo(folder: Path, repo: Optional[str]) -> Optional[str]:
    """The session's repository, recording `repo` as it when the session has none yet. Only that
    repository's pull requests take the short `#<number>` form, so two repositories never share a
    reference."""
    path = folder / "session.json"
    recorded = (read_json(path) or {}).get("repo")
    if isinstance(recorded, str):
        return recorded
    if repo:
        write_json(path, {"repo": repo})
    return repo


def ask(session: str, question: str, recommend: str, options: list[str], *, detail: str = "",
        pr: Optional[str] = None, agent: Optional[str] = None, repo: Optional[str] = None) -> dict:
    """Check, number, and record a question; returns it. `repo` is the repository of the checkout
    `ask` runs in, which becomes the session's repository when the session has none yet."""
    check(question, recommend, options, pr)
    folder = session_dir(session)
    pull = PULL.match(pr) if pr else None
    key = f"{pull['repo'].lower()}#{pull['number']}" if pull else "q"
    with locked(folder):
        home = session_repo(folder, repo)
        counts_path = folder / "counts.json"
        counts = read_json(counts_path) or {}
        count = int(counts.get(key, 0)) + 1
        record = {
            "ref": reference(pull, count, home),
            "number": count,
            "pr": pr,
            "askedAt": now_ms(),
            "question": question.strip(),
            "detail": detail.strip(),
            "choices": [recommend.strip(), *(option.strip() for option in options)],
        }
        if agent:
            record["agentId"] = agent
        counts[key] = count
        # The count lands first: a write that stops between the two then skips a
        # number, where the other order would hand the next question this reference.
        write_json(counts_path, counts)
        write_json(question_file(folder, record["ref"]), record)
    return record


# Finding a question

def parse_reference(text: str) -> tuple[str, Optional[str]]:
    match = REFERENCE.match(text.strip())
    if not match:
        raise QuestionError(f"{text!r} is not a question reference, such as #337.3, owner/repo#337.3, or q2, "
                            "with an optional answer letter, such as #337.3.a")
    return match["ref"], match["letter"]


def locate(session: Optional[str], ref: str) -> Path:
    """The folder holding `ref`: the session's, or, without one, the one session that has it."""
    if session is not None:
        folder = session_dir(session)
        if not question_file(folder, ref).is_file():
            raise QuestionNotFound(f"session {session} has no question {ref}")
        return folder
    holders = [name for name in sessions() if question_file(store_root() / name, ref).is_file()]
    if not holders:
        raise QuestionNotFound(f"no session has a question {ref}")
    if len(holders) > 1:
        raise AmbiguousQuestion(f"{ref} names a question in {len(holders)} sessions: {', '.join(holders)}; "
                                "pass --session")
    return store_root() / holders[0]


def find(session: Optional[str], ref: str) -> tuple[Path, dict]:
    folder = locate(session, ref)
    question = read_json(question_file(folder, ref))
    if question is None:
        raise QuestionNotFound(f"{question_file(folder, ref)} is not a question")
    return folder, question


def typed_letter(question: dict) -> str:
    return chr(ord("a") + len(question["choices"]))


def answer_ref(question: dict) -> str:
    answered = question.get("answer") or {}
    return question["ref"] if answered.get("dismissed") else f"{question['ref']}.{answered['letter']}"


def message(question: dict) -> str:
    """The line that delivers an answer, as `watch` prints it and the band sends it."""
    answered = question["answer"]
    if answered.get("dismissed"):
        return f"DISMISSED {question['ref']} {question['question']}"
    return f"ANSWER {answer_ref(question)} {question['question']} → {answered['text']}"


# Answering and delivering

def update(session: Optional[str], ref: str, change) -> dict:
    """Read the question under the lock, let `change` alter it, and write it back."""
    folder = locate(session, ref)
    with locked(folder):
        question = read_json(question_file(folder, ref))
        if question is None:
            raise QuestionNotFound(f"{question_file(folder, ref)} is not a question")
        change(question)
        write_json(question_file(folder, ref), question)
        if question.get("deliveredAt"):
            prune(folder)
    return question


def answer(session: Optional[str], ref: str, letter: Optional[str], text: Optional[str] = None) -> dict:
    def record(question: dict) -> None:
        if question.get("answer"):
            raise QuestionError(f"{question['ref']} is already answered, as {answer_ref(question)}")
        last = typed_letter(question)
        if letter is None:
            raise QuestionError(f"name the answer's letter, such as {question['ref']}.a")
        if letter == last:
            if not (text or "").strip():
                raise QuestionError(f"{question['ref']}.{last} is a typed answer; give it with --text")
            chosen = text.strip()
        elif text is not None:
            raise QuestionError(f"--text goes with the typed answer, {question['ref']}.{last}")
        elif ord(letter) - ord("a") < len(question["choices"]):
            chosen = question["choices"][ord(letter) - ord("a")]
        else:
            raise QuestionError(f"{question['ref']} has no answer {letter}; its letters run a to {last}")
        question["answer"] = {"letter": letter, "text": chosen, "answeredAt": now_ms()}

    return update(session, ref, record)


def dismiss(session: Optional[str], ref: str) -> dict:
    def record(question: dict) -> None:
        if question.get("answer"):
            raise QuestionError(f"{question['ref']} is already answered, as {answer_ref(question)}")
        question["answer"] = {"dismissed": True, "answeredAt": now_ms()}

    return update(session, ref, record)


def live_claim(question: dict) -> Optional[dict]:
    claim = question.get("claim")
    if claim and now_ms() - int(claim.get("renewedAt", 0)) < CLAIM_EXPIRES_MS:
        return claim
    return None


def claim(session: Optional[str], ref: str, renew: Optional[str] = None) -> dict:
    """Claim an answer for delivery, or renew a claim by its token; returns the question."""
    def take(question: dict) -> None:
        if not question.get("answer"):
            raise QuestionError(f"{question['ref']} has no answer to deliver")
        if question.get("deliveredAt"):
            raise QuestionError(f"{answer_ref(question)} is already delivered")
        held = live_claim(question)
        if renew is not None:
            if (question.get("claim") or {}).get("token") != renew:
                raise QuestionError(f"{answer_ref(question)} is not claimed with that token")
            question["claim"]["renewedAt"] = now_ms()
            return
        if held:
            raise QuestionError(f"{answer_ref(question)} is already claimed for delivery")
        question["claim"] = {"token": secrets.token_hex(8), "renewedAt": now_ms()}

    return update(session, ref, take)


def settle(session: Optional[str], ref: str, token: str, delivered: bool) -> dict:
    """Mark a claimed answer delivered, or reopen the question when its delivery failed."""
    def finish(question: dict) -> None:
        if question.get("deliveredAt"):
            raise QuestionError(f"{answer_ref(question)} is already delivered")
        if (question.get("claim") or {}).get("token") != token:
            raise QuestionError(f"{question['ref']} is not claimed with that token")
        del question["claim"]
        if delivered:
            question["deliveredAt"] = now_ms()
        else:
            del question["answer"]

    return update(session, ref, finish)


def prune(folder: Path) -> None:
    delivered = [question for question in load_questions(folder) if question.get("deliveredAt")]
    delivered.sort(key=lambda question: question["deliveredAt"], reverse=True)
    for question in delivered[KEEP_DELIVERED:]:
        with contextlib.suppress(FileNotFoundError):
            question_file(folder, question["ref"]).unlink()


def undelivered(session: str) -> list[dict]:
    return [question for question in load_questions(session_dir(session))
            if question.get("answer") and not question.get("deliveredAt") and not live_claim(question)]


def deliver_pass(session: str, send: Callable[[str], None]) -> None:
    """One pass of `watch`: claim each undelivered answer, `send` its line, and only then mark it
    delivered, so an answer whose line fails to send keeps just a claim, which lapses for the next
    deliverer."""
    for question in undelivered(session):
        try:
            claimed = claim(session, question["ref"])
        except QuestionError:
            continue
        send(message(claimed))
        settle(session, claimed["ref"], claimed["claim"]["token"], delivered=True)


# References in prose

def references_in(text: str) -> list[tuple[str, Optional[str]]]:
    seen: list[tuple[str, Optional[str]]] = []
    for match in IN_TEXT.finditer(text):
        found = (match["ref"], match["letter"])
        if found not in seen:
            seen.append(found)
    return seen


def context(session: str, text: str) -> list[str]:
    """What each reference in `text` means, for the questions the session holds."""
    folder = session_dir(session)
    lines = []
    for ref, letter in references_in(text):
        question = read_json(question_file(folder, ref))
        if question is None:
            continue
        if letter is None:
            lines.append(f"{ref} is the question: {question['question']}")
            continue
        index = ord(letter) - ord("a")
        answered = question.get("answer") or {}
        if index < len(question["choices"]):
            lines.append(f"{ref}.{letter} answers {question['question']} with {question['choices'][index]}")
        elif letter == typed_letter(question) and answered.get("letter") == letter:
            lines.append(f"{ref}.{letter} answers {question['question']} with {answered['text']}")
        elif letter == typed_letter(question):
            lines.append(f"{ref}.{letter} is a typed answer to {question['question']}")
    return lines
