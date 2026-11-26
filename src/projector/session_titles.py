"""Read native session titles without loading conversations into the console."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import watch

READ_BYTES = 256 * 1024


@dataclass
class TitleFile:
    signature: tuple | None = None
    offset: int = 0
    partial: bool = False
    skipping: bool = False
    titles: dict[str, str] = field(default_factory=dict)

    def read(self, path: Path, identifier: str, title_key: str) -> dict[str, str]:
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature == self.signature and (self.offset >= stat.st_size or self.partial):
                return self.titles
            if self.signature is not None and signature != self.signature and (
                    signature[:2] != self.signature[:2] or stat.st_size <= self.signature[2]):
                self.offset = 0
                self.skipping = False
                self.titles.clear()
            with path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(READ_BYTES)
        except OSError:
            return self.titles
        self.signature = signature
        complete = chunk.rfind(b"\n") + 1
        self.partial = len(chunk) < READ_BYTES and complete < len(chunk)
        if not complete:
            if len(chunk) == READ_BYTES:
                self.offset += len(chunk)
                self.skipping = True
            return self.titles
        self.offset += complete
        lines = chunk[:complete].splitlines()
        if self.skipping:
            lines = lines[1:]
            self.skipping = False
        marker = ('"' + title_key + '"').encode()
        for line in lines:
            if marker not in line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict):
                continue
            name, title = record.get(identifier), record.get(title_key)
            if isinstance(name, str) and isinstance(title, str) and title.strip():
                self.titles[name] = title.strip()
        return self.titles


class SessionTitles:
    def __init__(self) -> None:
        self.files: dict[Path, TitleFile] = {}
        self.claude_paths: dict[str, Path] = {}

    def read(self, sessions: dict[str, str]) -> dict[str, str]:
        found: dict[str, str] = {}
        active_paths: set[Path] = set()
        if "codex" in sessions.values():
            home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
            path = home / "session_index.jsonl"
            active_paths.add(path)
            titles = self.files.setdefault(path, TitleFile()).read(path, "id", "thread_name")
            found.update({name: title for name, title in titles.items() if sessions.get(name) == "codex"})
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        for name, host in sessions.items():
            if host != "claude" or not watch.SESSION.fullmatch(name):
                continue
            path = self.claude_paths.get(name)
            if path is None or not path.is_file():
                try:
                    path = max(root.glob(f"*/{name}.jsonl"), key=lambda one: one.stat().st_mtime_ns, default=None)
                except OSError:
                    path = None
                if path is None:
                    continue
                self.claude_paths[name] = path
            active_paths.add(path)
            titles = self.files.setdefault(path, TitleFile()).read(path, "sessionId", "customTitle")
            if name in titles:
                found[name] = titles[name]
        self.files = {path: data for path, data in self.files.items() if path in active_paths}
        self.claude_paths = {name: path for name, path in self.claude_paths.items() if name in sessions}
        return found
