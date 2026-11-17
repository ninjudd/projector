"""A session's conversation as its own chat shows it: the person's messages and the agent's text replies.

Claude Code and Codex each write a session's conversation to a JSON Lines transcript. `Reader` follows one,
reading only the bytes added since its last read, and turns each entry into what was said: the person's typed
prompts, the inbox lines Projector delivered on their behalf, and the agent's text. Tool calls, tool results,
thinking, reasoning, hook context, and instructions are left out, and so is an entry of a kind the reader does not
know, so a host that adds one breaks nothing.

The transcript holds whatever the conversation held, including any secret an agent quoted there, so the console
shows only what the session's own chat already shows, to the person who can already read that chat.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import codex_watch, watch

# How much of a transcript the first read takes from its end, so a long session opens at once.
TAIL_BYTES = 2 * 1024 * 1024
# How many things said a reader keeps, the newest.
KEEP = 400
# The lines Projector's deliverers send a session on the person's behalf, as `inbox.message` writes them.
INBOX_LINE = re.compile(r"^(ANSWER|DISCUSS|STARTED|UNCLEAR|RESTORED|DONE|DECLINED|DISMISSED) \d+\S* ")
COMMAND = re.compile(r"<command-name>(.*?)</command-name>(?:.*?<command-args>(.*?)</command-args>)?", re.S)


@dataclass(frozen=True)
class Said:
    """One thing said in the conversation: by `you`, the person, or by the `agent`."""

    who: str
    text: str


def inbox_lines(text: object) -> list[Said]:
    """The person's inbox lines in a prompt Projector delivered, leaving out its pull request events."""
    if not isinstance(text, str):
        return []
    return [Said("you", line) for line in text.splitlines() if INBOX_LINE.match(line)]


# Claude Code

def claude_typed(text: str) -> Optional[str]:
    """What the person typed, from a prompt's text: a slash command as `/name args`, and nothing for the output a
    command or a shell escape left in the conversation."""
    said = text.strip()
    if said.startswith("<command-"):
        found = COMMAND.search(said)
        return " ".join(part.strip() for part in found.groups() if part and part.strip()) if found else None
    if not said or said.startswith("<") or said.startswith("[Request interrupted"):
        return None
    return said


def claude_said(entry: dict) -> list[Said]:
    """What a Claude Code transcript entry said: an assistant entry's text blocks, the person's typed prompt, or
    the inbox lines in a prompt Projector's panel submitted. A subagent's, a meta, or a compaction summary's entry
    says nothing in the session's chat."""
    if entry.get("isSidechain") or entry.get("isMeta") or entry.get("isCompactSummary"):
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
    if entry.get("type") == "assistant":
        return [Said("agent", block["text"].strip()) for block in blocks
                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip()]
    if entry.get("type") != "user" or entry.get("toolUseResult") is not None:
        return []
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    if origin.get("kind") == "plugin":
        prompt = entry.get("pluginPrompt") if isinstance(entry.get("pluginPrompt"), dict) else {}
        return inbox_lines(prompt.get("text")) if origin.get("name") == "projector" else []
    # An older Claude Code writes no origin, so a prompt without one is taken as typed.
    if origin.get("kind") not in (None, "human"):
        return []
    # A tool's result sits in a `tool_result` block, whose text this never reads.
    text = content if isinstance(content, str) else "\n".join(
        block["text"] for block in blocks if block.get("type") == "text" and isinstance(block.get("text"), str))
    typed = claude_typed(text)
    return [Said("you", typed)] if typed else []


def claude_transcript(session: str, environ: Optional[dict] = None) -> Optional[Path]:
    """The transcript Claude Code writes for `session`, `projects/<project>/<session>.jsonl` under its
    configuration directory, the newest when several projects hold one."""
    environ = os.environ if environ is None else environ
    if not watch.SESSION.fullmatch(session):
        return None
    roots = [Path(environ["CLAUDE_CONFIG_DIR"]) / "projects"] if environ.get("CLAUDE_CONFIG_DIR") else []
    roots.append(Path(environ.get("HOME") or Path.home()) / ".claude" / "projects")
    found = []
    for root in roots:
        try:
            found += [path for path in root.glob(f"*/{session}.jsonl") if path.is_file()]
        except OSError:
            continue
    return max(found, key=lambda path: path.stat().st_mtime_ns, default=None)


# Codex

def codex_said(entry: dict) -> list[Said]:
    """What a Codex rollout entry said: an assistant message's text, or a user message's text that the person
    gave. Each content block of a user message carries its kind in `content_item_kinds`, where `user.text` is the
    person's and the rest are instructions and context Codex adds. A rollout that records no kinds is older, and
    there a block that opens with a tag is that context. A batch Projector's Codex adapter queued keeps only its
    inbox lines."""
    if entry.get("type") != "response_item":
        return []
    payload = entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return []
    blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
    texts = [(index, block.get("text")) for index, block in enumerate(blocks)
             if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip()]
    if payload.get("role") == "assistant":
        return [Said("agent", text.strip()) for _, text in texts]
    if payload.get("role") != "user":
        return []
    passthrough = payload.get("internal_chat_message_metadata_passthrough")
    kinds = passthrough.get("content_item_kinds") if isinstance(passthrough, dict) else None
    said = []
    for index, text in texts:
        if isinstance(kinds, list):
            if index >= len(kinds) or kinds[index] != "user.text":
                continue
        elif text.lstrip().startswith("<"):
            continue
        if codex_watch.MARKER.match(text):
            said += inbox_lines(text)
        else:
            said.append(Said("you", text.strip()))
    return said


def codex_transcript(root: Path, session: str) -> Optional[Path]:
    """The rollout `codex.json` names for the session's own thread."""
    path = codex_watch.registration(watch.session_folder(root, session)).get("transcript_path")
    return Path(path) if isinstance(path, str) and path and Path(path).is_absolute() else None


# Following a transcript

class Reader:
    """Follows one session's transcript. `read` returns what was said since the last read, and only this reader's
    one caller may call it, since it keeps the offset."""

    def __init__(self, locate: Callable[[], Optional[Path]], parse: Callable[[dict], list[Said]]) -> None:
        self.locate, self.parse = locate, parse
        self.path: Optional[Path] = None
        self.identity: Optional[tuple[int, int]] = None
        self.offset = 0
        self.partial = b""

    def read(self) -> tuple[bool, list[Said]]:
        """Whether the transcript started over, as when it was found, replaced, or cut short, so what was said
        before no longer stands, and what was said since. The first read of a transcript takes only its last
        `TAIL_BYTES`, from the first whole line in them."""
        path = self.path if self.path is not None and self.path.exists() else self.locate()
        if path is None:
            restarted = self.path is not None
            self.path, self.identity = None, None
            return restarted, []
        try:
            stat = path.stat()
            restarted = path != self.path or (stat.st_dev, stat.st_ino) != self.identity or stat.st_size < self.offset
            if restarted:
                self.path, self.identity, self.partial = path, (stat.st_dev, stat.st_ino), b""
                # A line the tail starts inside is the end of a JSON object, which is never JSON itself, so it
                # is skipped below with any other line that does not parse.
                self.offset = max(0, stat.st_size - TAIL_BYTES)
            with path.open("rb") as handle:
                handle.seek(self.offset)
                added = handle.read()
        except OSError:
            return False, []
        self.offset += len(added)
        *lines, self.partial = (self.partial + added).split(b"\n")
        said = []
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                said += self.parse(entry)
        return restarted, said


def reader_for(root: Path, session: str, host: str) -> Reader:
    """A reader of the session's conversation: Codex's rollout for a Codex session, and otherwise Claude Code's
    transcript."""
    if host == "codex":
        return Reader(lambda: codex_transcript(root, session), codex_said)
    return Reader(lambda: claude_transcript(session), claude_said)
