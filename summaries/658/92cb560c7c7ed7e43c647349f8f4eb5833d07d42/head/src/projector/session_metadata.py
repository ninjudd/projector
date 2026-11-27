"""Read native session metadata without loading conversations into the console."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import watch

READ_BYTES = 256 * 1024


@dataclass
class MetadataFile:
    signature: tuple | None = None
    offset: int = 0
    partial: bool = False
    skipping: bool = False
    records: dict[str, dict[str, str]] = field(default_factory=dict)

    def read(self, path: Path, identifier: str, keys: tuple[str, ...]) -> dict[str, dict[str, str]]:
        try:
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature == self.signature and (self.offset >= stat.st_size or self.partial):
                return self.records
            if self.signature is not None and signature != self.signature and (
                    signature[:2] != self.signature[:2] or stat.st_size <= self.signature[2]):
                self.offset = 0
                self.skipping = False
                self.records.clear()
            with path.open("rb") as stream:
                stream.seek(self.offset)
                chunk = stream.read(READ_BYTES)
        except OSError:
            return self.records
        self.signature = signature
        complete = chunk.rfind(b"\n") + 1
        self.partial = len(chunk) < READ_BYTES and complete < len(chunk)
        if not complete:
            if len(chunk) == READ_BYTES:
                self.offset += len(chunk)
                self.skipping = True
            return self.records
        self.offset += complete
        lines = chunk[:complete].splitlines()
        if self.skipping:
            lines = lines[1:]
            self.skipping = False
        markers = [('"' + key + '"').encode() for key in keys]
        for line in lines:
            if not any(marker in line for marker in markers):
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict):
                continue
            name = record.get(identifier)
            if not isinstance(name, str) or record.get("isSidechain") is True:
                continue
            for key in keys:
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    self.records.setdefault(name, {})[key] = value
        return self.records


@dataclass(frozen=True)
class Metadata:
    title: str
    host: str
    cwd: str = ""


class SessionMetadata:
    def __init__(self) -> None:
        self.files: dict[Path, MetadataFile] = {}
        self.claude_paths: dict[str, Path] = {}

    def read(self, sessions: dict[str, str]) -> dict[str, Metadata]:
        found: dict[str, Metadata] = {}
        active_paths: set[Path] = set()
        if "codex" in sessions.values():
            home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
            path = home / "session_index.jsonl"
            active_paths.add(path)
            records = self.files.setdefault(path, MetadataFile()).read(path, "id", ("thread_name",))
            found.update({name: Metadata(record["thread_name"].strip(), "codex") for name, record in records.items()
                          if sessions.get(name) == "codex"})
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
        for name, host in sessions.items():
            if host == "codex" or not watch.SESSION.fullmatch(name):
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
            records = self.files.setdefault(path, MetadataFile()).read(
                path, "sessionId", ("customTitle", "aiTitle", "agentName", "cwd"))
            record = records.get(name)
            if record:
                title = record.get("customTitle") or record.get("aiTitle") or record.get("agentName", "")
                cwd = record.get("cwd", "")
                found[name] = Metadata(title.strip(), "claude", cwd if "\0" not in cwd and Path(cwd).is_absolute() else "")
        self.files = {path: data for path, data in self.files.items() if path in active_paths}
        self.claude_paths = {name: path for name, path in self.claude_paths.items() if name in sessions}
        return found
