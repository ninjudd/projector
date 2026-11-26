from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from .core import EnvironmentError
from .inbox import locked


class Store:
    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "runtime.sqlite"
        with locked(folder):
            if self.path.exists():
                with self.path.open("rb") as stream:
                    if stream.read(16) != b"SQLite format 3\0":
                        raise EnvironmentError("Cannot read session execution state: invalid database header")
            with self.connection() as db:
                db.executescript('''
                CREATE TABLE IF NOT EXISTS commands (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE, text TEXT NOT NULL, source TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', generation TEXT, turn TEXT, outcome TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT, kind TEXT NOT NULL, payload TEXT NOT NULL, at REAL NOT NULL, size INTEGER NOT NULL,
                    generation TEXT, turn TEXT);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                ''')
                columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
                for column in ("generation", "turn"):
                    if column not in columns:
                        db.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        try:
            with sqlite3.connect(self.path, timeout=5) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA synchronous=FULL")
                yield db
        except sqlite3.DatabaseError as error:
            raise EnvironmentError(f"Cannot read session execution state: {error}") from error

    def value(self, key: str, default=None):
        with self.connection() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, **values) -> None:
        with self.connection() as db:
            db.executemany("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(key, json.dumps(value)) for key, value in values.items()])

    def request_control(self, ident: str, action: str) -> None:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {row["key"]: json.loads(row["value"]) for row in db.execute(
                "SELECT key,value FROM state WHERE key IN ('control','control_result')")}
            pending, result = values.get("control", {}), values.get("control_result", {})
            if pending and pending["id"] != result.get("id"):
                raise EnvironmentError("Another runtime control is pending; wait for its result")
            db.execute("INSERT INTO state VALUES ('control',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps({"id": ident, "action": action}),))

    def enqueue(self, ident: str, text: str, source: list) -> None:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO commands(id,text,source) VALUES (?,?,?)",
                       (ident, text, json.dumps(source)))

    def commands(self, states: tuple[str, ...] = ()) -> list[dict]:
        where = " WHERE state IN (" + ",".join("?" for _ in states) + ")" if states else ""
        with self.connection() as db:
            found = db.execute("SELECT * FROM commands" + where + " ORDER BY sequence", states).fetchall()
        return [{**dict(row), "source": json.loads(row["source"])} for row in found]

    def command(self, ident: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM commands WHERE id=?", (ident,)).fetchone()
        return {**dict(row), "source": json.loads(row["source"])} if row else None

    def begin(self, ident: str, generation: str) -> dict | None:
        with self.connection() as db:
            changed = db.execute("UPDATE commands SET state='submitting',generation=? WHERE id=? AND state='queued'",
                                 (generation, ident)).rowcount
            if not changed:
                return None
            row = dict(db.execute("SELECT * FROM commands WHERE id=?", (ident,)).fetchone())
            self.append(db, "user", {"text": row["text"]}, ident)
        return row

    def accepted(self, ident: str, generation: str, turn: str | None = None) -> bool:
        with self.connection() as db:
            return bool(db.execute("UPDATE commands SET state='accepted',turn=? "
                "WHERE id=? AND generation=? AND state='submitting'", (turn, ident, generation)).rowcount)

    def finish(self, ident: str, generation: str, outcome: str, error=None) -> bool:
        with self.connection() as db:
            changed = db.execute("UPDATE commands SET state='finished',outcome=? "
                "WHERE id=? AND generation=? AND state IN ('submitting','accepted')",
                (outcome, ident, generation)).rowcount
            if changed:
                self.append(db, "turn_end", {"outcome": outcome, "error": error}, ident)
            return bool(changed)

    def recover(self) -> None:
        with self.connection() as db:
            db.execute("UPDATE commands SET state='uncertain' WHERE state IN ('submitting','accepted')")

    def resolve(self, ident: str, retry: bool) -> None:
        with self.connection() as db:
            if not db.execute("UPDATE commands SET state=?,outcome=? WHERE id=? AND state='uncertain'",
                              ("queued" if retry else "finished", None if retry else "confirmed", ident)).rowcount:
                raise EnvironmentError("This command no longer needs recovery")

    def edit(self, ident: str, text: str | None) -> None:
        with self.connection() as db:
            if text is None:
                changed = db.execute("UPDATE commands SET state='finished',outcome='cancelled' "
                                     "WHERE id=? AND state='queued'", (ident,)).rowcount
            else:
                changed = db.execute("UPDATE commands SET text=? WHERE id=? AND state='queued'", (text, ident)).rowcount
            if not changed:
                raise EnvironmentError("This input has already been submitted; interrupt the turn instead")

    def event(self, kind: str, payload: dict, command: str | None = None) -> None:
        with self.connection() as db:
            self.append(db, kind, payload, command)

    @staticmethod
    def append(db: sqlite3.Connection, kind: str, payload: dict, command: str | None) -> None:
        encoded = json.dumps(payload, ensure_ascii=False)
        data = encoded.encode("utf-8")
        if len(data) > 64 * 1024:
            if kind in ("text", "user"):
                payload = {"text": str(payload.get("text", ""))[:8192], "truncated": True}
            elif kind == "turn_end":
                payload = {"outcome": payload.get("outcome"), "error": str(payload.get("error", ""))[:8192],
                           "truncated": True}
            else:
                item = payload.get("item", {})
                payload = {"item": {key: item[key] for key in ("id", "tool_use_id", "name", "type") if key in item},
                           "preview": data[:16384].decode("utf-8", errors="replace"), "truncated": True}
            encoded = json.dumps(payload, ensure_ascii=False)
        context = db.execute("SELECT generation,turn FROM commands WHERE id=?", (command,)).fetchone()
        generation, turn = (context["generation"], context["turn"]) if context else (None, None)
        cursor = db.execute("INSERT INTO events(command,kind,payload,at,size,generation,turn) VALUES (?,?,?,?,?,?,?)",
                            (command, kind, encoded, time.time(), len(encoded.encode("utf-8")), generation, turn))
        if cursor.lastrowid % 100 == 0:
            db.execute("""DELETE FROM events WHERE sequence IN (
                SELECT sequence FROM (
                    SELECT sequence, SUM(size) OVER (ORDER BY sequence DESC) AS bytes,
                           ROW_NUMBER() OVER (ORDER BY sequence DESC) AS count FROM events)
                WHERE bytes > 8388608 OR count > 5000)""")

    def events(self, after: int = 0, limit: int = 5000) -> list[dict]:
        with self.connection() as db:
            found = db.execute("SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in found]
