"""Change a loop's tracked file: add a pull request, forget a finished one, keep one out, or let it back in.

A tracked file holds one `owner/repo#number` per line, and a line
`!owner/repo#number` keeps that pull request out of the loop wherever it
sits in the file. Both watchers and the band honor such a line, matching
the reference without regard to case, as GitHub does.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .core import ProjectorError, write_through
from .inbox import PULL


class TrackedFileNotFound(ProjectorError):
    exit_code = 66


def check_reference(reference: str) -> None:
    """Refuses a reference that is not a well-formed `owner/repo#number`."""

    if not PULL.match(reference):
        raise ProjectorError(f"{reference!r} is not owner/repo#number")


def read(tracked: Path) -> str:
    try:
        return tracked.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise TrackedFileNotFound(f"no tracked file at {tracked}") from error


def is_entry(line: str, reference: str) -> bool:
    return line.strip().lower() == reference.lower()


def is_exclusion(line: str, reference: str) -> bool:
    text = line.strip()
    return text.startswith("!") and is_entry(text[1:], reference)


def append(tracked: Path, text: str, line: str) -> None:
    """Append `line` to the tracked file whose content is `text`, on a line of its own.

    The line is appended rather than the file rewritten, because a watcher
    may be appending a line it adopted at the same moment.
    """

    separator = "\n" if text and not text.endswith("\n") else ""
    with tracked.open("a", encoding="utf-8") as stream:
        stream.write(f"{separator}{line}\n")


def add(tracked: Path, reference: str) -> bool:
    """Append `reference` to the tracked file, creating the file, answering whether the file changed.

    A pull request the file drops is refused rather than listed, because its
    `!reference` line would keep it out of the loop all the same.
    """

    check_reference(reference)
    try:
        text = tracked.read_text(encoding="utf-8")
    except FileNotFoundError:
        tracked.parent.mkdir(parents=True, exist_ok=True)
        text = ""
    lines = text.splitlines()
    if any(is_exclusion(line, reference) for line in lines):
        raise ProjectorError(f"{reference} is dropped from {tracked}; run `project loop restore` to let it back in")
    if any(is_entry(line, reference) for line in lines):
        return False
    append(tracked, text, reference)
    return True


def drop(tracked: Path, reference: str) -> bool:
    """Append `!reference` to the tracked file, answering whether the file changed."""

    check_reference(reference)
    text = read(tracked)
    if any(is_exclusion(line, reference) for line in text.splitlines()):
        return False
    append(tracked, text, f"!{reference}")
    return True


def rewrite_without(tracked: Path, reference: str, matches: Callable[[str, str], bool]) -> bool:
    check_reference(reference)
    lines = read(tracked).splitlines(keepends=True)
    kept = [line for line in lines if not matches(line, reference)]
    if len(kept) == len(lines):
        return False
    write_through(tracked, "".join(kept))
    return True


def restore(tracked: Path, reference: str) -> bool:
    """Remove every `!reference` line from the tracked file, answering whether the file changed."""

    return rewrite_without(tracked, reference, is_exclusion)


def remove(tracked: Path, reference: str) -> bool:
    """Remove every plain `reference` line from the tracked file, answering whether the file changed.

    A `!reference` line stays, so a pull request kept out of the loop stays
    out, even from a review loop that would adopt it again if it reopened.
    """

    return rewrite_without(tracked, reference, is_entry)
