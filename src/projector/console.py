"""`project console`: every live session in one terminal, and each session's panel.

The console opens on the home list, one entry per live session, sorted by what needs the person. Enter opens a
session's panel: its pull request rows, as `rows.py` reads them, and its inbox items, each with a drawer that
answers, discusses, dismisses, or snoozes it. Every choice is recorded with the functions `project inbox` runs,
for the session on screen, and the session's own deliverer brings it in: Claude Code's panel, or the watch
daemon's Codex adapter. The console never claims, delivers, or settles a line, so it never races a deliverer.

`Console` holds what is on screen, turns key names into those calls, and lays the screen out as lines of styled
text. `main` puts it in a terminal with curses, which only `main` imports, so tests drive `Console` without one.
"""

from __future__ import annotations

import os
import textwrap
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import agents, codex_watch, inbox, rows, watch
from .core import ProjectorError

INBOX_EVERY = 1.0
ROWS_EVERY = 5.0
THREADS_EVERY = 30.0
# How long a resolved, delivered, or snoozed item stays listed, as in the panel.
RESOLVED_MS = 30_000
# The host each deliverer `watch.delivery` names belongs to.
HOSTS = {"mod": "claude", "codex": "codex"}
KINDS = ("question", "request", "notice", "discussion")
ASKING_KINDS = ("question", "request")
NOUNS = {"pr": "pull request", "review": "review request", **{kind: kind for kind in KINDS}}
SNOOZES = (("15 min", 15), ("1 hour", 60), ("4 hours", 240), ("1 day", 1440))
SUBSCRIPTION_ROLE = {held: role for role, held in rows.ROLE_OF.items()}
DRAWER_INDENT = 4


@dataclass
class Session:
    """A live session as the console shows it."""

    name: str
    host: str
    label: str
    place: str
    summary: str
    signature: Optional[tuple] = None
    items: list[dict] = field(default_factory=list)
    rows: list[rows.Row] = field(default_factory=list)


@dataclass
class Field:
    """The one-line text field: what sending it records, and the words typed so far with the cursor in them."""

    purpose: str
    target: Optional[str]
    prompt: str
    text: str = ""
    cursor: int = 0


@dataclass(frozen=True)
class Control:
    """A key a drawer offers, its label, what pressing it does, and whether it is one of the item's options, which
    the drawer lists one to a line above its other keys."""

    key: str
    label: str
    act: Callable[[], None]
    option: bool = False


@dataclass
class Line:
    """One line of the screen: its parts, each text with a style name, and whether the cursor's row is on it."""

    parts: list[tuple[str, str]]
    selected: bool = False

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.parts)


@dataclass
class Work:
    """What one refresh reads, decided under the lock and done outside it."""

    inbox: bool
    rows: bool
    signatures: dict[str, Optional[tuple]]
    pull: Optional[rows.Pull] = None
    drawer: Optional[str] = None


@dataclass
class Found:
    """What a refresh read."""

    live: Optional[dict[str, tuple[str, str, str, str, tuple, Optional[list[dict]]]]] = None
    rows: dict[str, list[rows.Row]] = field(default_factory=dict)
    drawer: Optional[str] = None
    threads: Optional[list[rows.Thread]] = None


# Reading the sessions

def live_sessions(root: Path, now: float) -> dict[str, str]:
    """Each session with an inbox or a watch folder whose deliverer is live, with how its events arrive."""
    found = {}
    for name in sorted(set(inbox.sessions()) | set(watch.session_names(root))):
        delivery = watch.delivery(root, name, now)
        if delivery != "none":
            found[name] = delivery
    return found


def place_of(record: dict, folder: Path, checkouts: dict[str, str]) -> str:
    """The repository and branch `agent.json` names, or those of the checkout `codex.json` runs in."""
    place = " ".join(part for part in (record.get("repo"), record.get("branch")) if part)
    cwd = codex_watch.registration(folder).get("cwd")
    if place or not isinstance(cwd, str) or not cwd:
        return place
    if cwd not in checkouts:
        try:
            found = agents.checkout(Path(cwd))
        except (ProjectorError, OSError):
            found = {}
        checkouts[cwd] = " ".join(part for part in (found.get("repo"), found.get("branch")) if part) or cwd
    return checkouts[cwd]


def describe(root: Path, name: str, delivery: str, checkouts: dict[str, str]) -> tuple[str, str, str, str]:
    """The session's label, host, place, and summary, as `project agent list` shows them."""
    record = agents.record(root, name) or {}
    host = HOSTS.get(delivery) or record.get("host") or "other"
    place = place_of(record, watch.session_folder(root, name), checkouts)
    return agents.label(record, name), host, place, record.get("summary") or ""


def signature(folder: Path) -> tuple:
    """The names, sizes, and modification times of the files in a session's inbox directory."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return ()
    found = []
    for entry in entries:
        try:
            stat = entry.stat()
        except OSError:
            continue
        found.append((entry.name, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(found))


# What an item shows

def is_snoozed(item: dict, now_ms: int) -> bool:
    until = item.get("snoozedUntil")
    return isinstance(until, int) and now_ms < until


def is_awake(item: dict, now_ms: int) -> bool:
    return not item.get("answer") and not is_snoozed(item, now_ms)


def is_shown(item: dict, now_ms: int) -> bool:
    """The panel's `isShownAt`: not withdrawn, not snoozed or snoozed in the last 30 seconds, and not delivered
    or delivered in the last 30 seconds."""
    if (item.get("answer") or {}).get("withdrawn"):
        return False
    snoozed_at = item.get("snoozedAt")
    if is_snoozed(item, now_ms) and not (isinstance(snoozed_at, int) and now_ms - snoozed_at < RESOLVED_MS):
        return False
    delivered = item.get("deliveredAt")
    return delivered is None or now_ms - delivered < RESOLVED_MS


def is_replied(item: dict) -> bool:
    return not item.get("answer") and inbox.has_unread_reply(item)


def stage_of(item: dict) -> Optional[str]:
    """An open request's start: `starting` until its agent reports, then `ready` or `blocked`."""
    if item["kind"] != "request" or item.get("answer"):
        return None
    return (item.get("progress") or {}).get("state") or ("starting" if item.get("started") else None)


def start_of(item: dict) -> Optional[str]:
    """What an open request's first control presses: Start, until it is pressed, or Retry once it is blocked."""
    if item["kind"] != "request" or item.get("answer") or item.get("start") is None:
        return None
    stage = stage_of(item)
    return "Start" if stage is None else "Retry" if stage == "blocked" else None


def marks_of(item: dict) -> list[tuple[str, str]]:
    """The words after an item's subject, as the panel shows them."""
    is_open = not item.get("answer")
    owes = inbox.owed_entry(item) is not None
    marks = []
    if (not is_open and not item.get("deliveredAt")) or owes:
        marks.append(("sending", "subtle"))
    if is_replied(item):
        marks.append(("replied", "replied"))
    if is_open and item.get("unclear"):
        marks.append(("unclear", "warning"))
    stage = stage_of(item)
    if stage is not None:
        marks.append((stage, {"starting": "subtle", "ready": "success", "blocked": "warning"}[stage]))
    if item.get("pr"):
        marks.append((item["pr"], "subtle"))
    return marks


def age(milliseconds: float) -> str:
    seconds = max(0, int(milliseconds // 1000))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def counted(count: int, noun: str) -> Optional[str]:
    return None if count == 0 else f"{count} {noun}{'' if count == 1 else 's'}"


# A session's entries and its place on the home list

def shown_items(session: Session, now_ms: int) -> list[dict]:
    """The items the session's panel lists, newest first."""
    return sorted((item for item in session.items if is_shown(item, now_ms)),
                  key=lambda item: item.get("postedAt", 0), reverse=True)


def counts_of(session: Session, now_ms: int) -> list[str]:
    """The panel header's counts: pull requests, review requests, then each kind's open, awake items."""
    awake = [item for item in session.items if is_shown(item, now_ms) and is_awake(item, now_ms)]
    parts = [counted(sum(1 for row in session.rows if row.section == section), NOUNS[section])
             for section in rows.SECTIONS]
    parts += [counted(sum(1 for item in awake if item["kind"] == kind), NOUNS[kind]) for kind in KINDS]
    return [part for part in parts if part is not None]


def needs(session: Session, now_ms: int) -> tuple:
    """The home list's order: an unread reply first, then the most open questions and requests, then the
    newest item, then the label."""
    shown = shown_items(session, now_ms)
    asking = sum(1 for item in shown if is_awake(item, now_ms) and item["kind"] in ASKING_KINDS)
    newest = max((item.get("postedAt", 0) for item in shown), default=0)
    return (not any(map(is_replied, shown)), -asking, -newest, session.label, session.name)


def headline(session: Session, now_ms: int) -> Optional[dict]:
    """The item a home entry names: the newest with an unread reply, or else the newest open one."""
    shown = shown_items(session, now_ms)
    return (next((item for item in shown if is_replied(item)), None)
            or next((item for item in shown if is_awake(item, now_ms)), None))


def pull_key(pull: rows.Pull) -> str:
    return f"pr:{pull.repo}#{pull.number}"


def item_key(item: dict) -> str:
    return f"item:{item['ref']}"


class Console:
    """What the console shows, and what each key does to it."""

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time,
                 gh: Callable[[list[str]], watch.GhResult] = watch.run_gh,
                 open_url: Callable[[str], Any] = webbrowser.open,
                 wake: Callable[[str, dict], None] = lambda session, item: None) -> None:
        self.root = root
        self.clock = clock
        self.gh = gh
        self.open_url = open_url
        self.wake = wake
        self.lock = threading.RLock()
        self.refresh_now = threading.Event()
        self.sessions: dict[str, Session] = {}
        self.checkouts: dict[str, str] = {}
        self.view = "home"
        self.session: Optional[str] = None
        self.selected: Optional[str] = None
        self.drawer: Optional[str] = None
        self.asking: Optional[str] = None
        self.field: Optional[Field] = None
        self.threads: Optional[list[rows.Thread]] = None
        self.status = ""
        self.quit = False
        self.next_inbox = self.next_rows = self.next_threads = float("-inf")

    # Refreshing

    def tick(self) -> None:
        """One refresh: decide under the lock what is due, read it without the lock, then show it."""
        with self.lock:
            work = self.due(self.clock())
        if work is not None:
            found = self.load(work)
            with self.lock:
                self.apply(found)

    def due(self, now: float) -> Optional[Work]:
        work = Work(inbox=now >= self.next_inbox, rows=now >= self.next_rows,
                    signatures={name: session.signature for name, session in self.sessions.items()})
        if work.inbox:
            self.next_inbox = now + INBOX_EVERY
        if work.rows:
            self.next_rows = now + ROWS_EVERY
        row = self.row(self.drawer)
        if row is not None and now >= self.next_threads:
            self.next_threads = now + THREADS_EVERY
            work.pull, work.drawer = row.pull, self.drawer
        return work if work.inbox or work.rows or work.pull else None

    def load(self, work: Work) -> Found:
        found = Found(drawer=work.drawer)
        names = list(work.signatures)
        if work.inbox:
            found.live = {}
            now = self.clock()
            for name, delivery in live_sessions(self.root, now).items():
                folder = inbox.store_root() / name
                seen = signature(folder)
                items = None if name in work.signatures and seen == work.signatures[name] else inbox.load_items(folder)
                found.live[name] = (*describe(self.root, name, delivery, self.checkouts), seen, items)
            names = list(found.live)
        for name in names:
            if work.rows or name not in work.signatures:
                found.rows[name] = rows.rows_for(self.root, name)
        if work.pull is not None:
            found.threads = rows.fetch_threads(work.pull, self.gh)
        return found

    def apply(self, found: Found) -> None:
        if found.live is not None:
            for name in set(self.sessions) - set(found.live):
                del self.sessions[name]
            for name, (label, host, place, summary, seen, items) in found.live.items():
                session = self.sessions.setdefault(name, Session(name, host, label, place, summary))
                session.host, session.label, session.place, session.summary = host, label, place, summary
                session.signature = seen
                if items is not None:
                    session.items = items
        for name, found_rows in found.rows.items():
            if name in self.sessions:
                self.sessions[name].rows = found_rows
        if found.drawer is not None and found.drawer == self.drawer:
            self.threads = found.threads
        self.settle_view()

    def reload(self) -> None:
        """Read the viewed session's items and rows again, after a key changed them."""
        session = self.sessions.get(self.session or "")
        if session is not None:
            folder = inbox.store_root() / session.name
            session.signature, session.items = signature(folder), inbox.load_items(folder)
            session.rows = rows.rows_for(self.root, session.name)
        self.settle_view()

    def settle_view(self) -> None:
        """Leave a panel whose session is gone, close a drawer whose row is gone, and mark read the replies an
        open drawer shows."""
        if self.view == "panel" and self.session not in self.sessions:
            self.status = f"{self.session} is no longer live"
            self.go_home()
            return
        if self.view != "panel":
            return
        keys = [key for key, _ in self.entries()]
        if self.drawer is not None and self.drawer not in keys:
            self.close_drawer()
        item = self.item(self.drawer)
        if item is not None and inbox.has_unread_reply(item):
            try:
                inbox.mark_read(self.session, item["ref"])
            except ProjectorError as error:
                self.status = str(error)
                return
            self.reload()

    # What is on screen

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def home(self) -> list[Session]:
        now = self.now_ms()
        return sorted(self.sessions.values(), key=lambda session: needs(session, now))

    def viewed(self) -> Optional[Session]:
        return self.sessions.get(self.session or "")

    def entries(self) -> list[tuple[str, object]]:
        """The viewed panel's rows: its pull requests, then its items, each under its key."""
        session = self.viewed()
        if session is None:
            return []
        return ([(pull_key(row.pull), row) for row in session.rows]
                + [(item_key(item), item) for item in shown_items(session, self.now_ms())])

    def keys(self) -> list[str]:
        if self.view == "home":
            return [session.name for session in self.home()]
        return [key for key, _ in self.entries()]

    def row(self, key: Optional[str]) -> Optional[rows.Row]:
        found = dict(self.entries()).get(key) if key and key.startswith("pr:") else None
        return found if isinstance(found, rows.Row) else None

    def item(self, key: Optional[str]) -> Optional[dict]:
        found = dict(self.entries()).get(key) if key and key.startswith("item:") else None
        return found if isinstance(found, dict) else None

    # Keys

    def key(self, name: str) -> None:
        """Do what the key named `name` does: a character, or `up`, `down`, `left`, `right`, `enter`, `esc`,
        `backspace`, `delete`, `home`, or `end`."""
        with self.lock:
            self.status = ""
            if self.field is not None:
                self.field_key(name)
            elif name == "q":
                self.quit = True
            elif name in ("up", "k", "down", "j"):
                self.move(-1 if name in ("up", "k") else 1)
            elif self.view == "home":
                if name == "enter" and self.selected_key() is not None:
                    self.open_panel(self.selected_key())
            else:
                self.panel_key(name)

    def selected_key(self) -> Optional[str]:
        keys = self.keys()
        return self.selected if self.selected in keys else (keys[0] if keys else None)

    def move(self, step: int) -> None:
        keys = self.keys()
        if not keys:
            return
        at = keys.index(self.selected) if self.selected in keys else 0
        self.selected = keys[max(0, min(len(keys) - 1, at + step))]
        if self.view == "panel":
            self.close_drawer()

    def open_panel(self, name: str) -> None:
        self.view, self.session, self.selected = "panel", name, None
        self.close_drawer()

    def go_home(self) -> None:
        self.close_drawer()
        self.field = None
        self.view, self.selected, self.session = "home", self.session, None

    def close_drawer(self) -> None:
        self.drawer = self.asking = None
        self.threads = None

    def panel_key(self, name: str) -> None:
        if name == "left" or (name in ("esc", "0") and self.drawer is None):
            self.go_home()
        elif name in ("esc", "0"):
            self.close_drawer()
        elif name == "enter":
            self.toggle_drawer()
        elif self.drawer is not None:
            control = next((one for one in self.controls() if one.key == name), None)
            if control is not None:
                control.act()
        elif name == "7":
            self.open_field("session", None, "Discuss this session: ")

    def toggle_drawer(self) -> None:
        key = self.selected_key()
        if key is None or key == self.drawer:
            self.close_drawer()
            return
        self.close_drawer()
        self.drawer = self.selected = key
        if key.startswith("pr:"):
            self.next_threads = float("-inf")
            self.refresh_now.set()
        self.settle_view()

    def controls(self) -> list[Control]:
        row, item = self.row(self.drawer), self.item(self.drawer)
        if row is not None:
            return self.pull_controls(row)
        return self.item_controls(item) if item is not None else []

    def pull_controls(self, row: rows.Row) -> list[Control]:
        pull = row.pull
        if self.asking == "dismiss":
            return [Control("8", "Yes, dismiss it", lambda: self.dismiss_pull(row)),
                    Control("9", "No, keep it", self.stop_asking)]
        reference = f"{pull.repo}#{pull.number}"
        controls = [Control("7", "Discuss", lambda: self.open_field("pr", reference, f"Discuss {reference}: ")),
                    Control("8", "Dismiss", lambda: self.ask("dismiss")),
                    Control("o", "Open in GitHub", lambda: self.open_url(pull.url))]
        if pull.summary_url is not None:
            controls.append(Control("p", "Open in Projector", lambda: self.open_url(pull.summary_url)))
        return controls

    def item_controls(self, item: dict) -> list[Control]:
        ref, now = item["ref"], self.now_ms()
        answered = item.get("answer")
        if answered:
            if answered.get("dismissed") and not answered.get("withdrawn"):
                return [Control("8", "Restore", lambda: self.record(inbox.restore, ref))]
            return []
        if self.asking == "snooze" and not is_snoozed(item, now):
            return [*(Control(str(index), label, lambda minutes=minutes: self.record(inbox.snooze, ref, minutes), True)
                      for index, (label, minutes) in enumerate(SNOOZES, 1)),
                    Control("9", "Cancel", self.stop_asking)]
        controls = []
        if item["kind"] == "question":
            controls += [Control(str(index), f"{choice}{' (recommended)' if index == 1 else ''}",
                                 lambda letter=chr(ord("a") + index - 1): self.record(inbox.answer, ref, letter), True)
                         for index, choice in enumerate(item["choices"], 1)]
            controls.append(Control("t", "type an answer", lambda: self.open_field("typed", ref, f"Answer {ref}: "),
                                    True))
        elif item["kind"] == "request":
            start = start_of(item)
            if start is not None:
                controls.append(Control("1", start, lambda: self.record(inbox.start, ref, item["start"]), True))
            given = item.get("choices")
            controls += [Control(str(index), choice,
                                 lambda choice=choice: self.record(inbox.done, ref, choice if given else None), True)
                         for index, choice in enumerate(given or ["Done"], 1 if start is None else 2)]
        if not item.get("unclear"):
            controls.append(Control("6", "I don't understand",
                                    lambda: self.record(inbox.unclear, ref, item["subject"], item.get("detail", ""))))
        controls += [Control("7", "Discuss", lambda: self.open_field("discuss", ref, f"Discuss {ref}: ")),
                     Control("8", "Dismiss", lambda: self.record(inbox.dismiss, ref)),
                     Control("9", "Unsnooze", lambda: self.record(inbox.restore, ref)) if is_snoozed(item, now)
                     else Control("9", "Snooze", lambda: self.ask("snooze"))]
        return controls

    def ask(self, what: str) -> None:
        self.asking = what

    def stop_asking(self) -> None:
        self.asking = None

    # Recording

    def record(self, change: Callable[..., dict], *args) -> Optional[dict]:
        """Record a choice with an inbox function for the viewed session, then show it. For a Codex session,
        wake the daemon, which delivers its lines."""
        session = self.viewed()
        if session is None:
            return None
        try:
            item = change(session.name, *args)
        except ProjectorError as error:
            self.status = str(error)
            return None
        self.asking = None
        if session.host == "codex":
            try:
                self.wake(session.name, item)
            except (ProjectorError, OSError) as error:
                self.status = f"recorded, but the watch daemon did not start: {error}"
        self.reload()
        return item

    def dismiss_pull(self, row: rows.Row) -> None:
        """Keep the pull request out of each role the viewed session holds on it, as `project watch drop` does."""
        session = self.viewed()
        if session is None:
            return
        self.asking = None
        try:
            for role in row.roles:
                watch.drop(self.root, session.name, row.pull.repo, row.pull.number, SUBSCRIPTION_ROLE[role])
        except ProjectorError as error:
            self.status = str(error)
        self.reload()

    # The text field

    def open_field(self, purpose: str, target: Optional[str], prompt: str) -> None:
        self.field = Field(purpose, target, prompt)

    def field_key(self, name: str) -> None:
        typing = self.field
        assert typing is not None
        if name == "esc":
            self.field = None
        elif name == "enter":
            self.send(typing)
        elif name == "left":
            typing.cursor = max(0, typing.cursor - 1)
        elif name == "right":
            typing.cursor = min(len(typing.text), typing.cursor + 1)
        elif name == "home":
            typing.cursor = 0
        elif name == "end":
            typing.cursor = len(typing.text)
        elif name == "backspace" and typing.cursor > 0:
            typing.text = typing.text[:typing.cursor - 1] + typing.text[typing.cursor:]
            typing.cursor -= 1
        elif name == "delete":
            typing.text = typing.text[:typing.cursor] + typing.text[typing.cursor + 1:]
        elif len(name) == 1 and name.isprintable():
            typing.text = typing.text[:typing.cursor] + name + typing.text[typing.cursor:]
            typing.cursor += 1

    def send(self, typing: Field) -> None:
        """Record what the field holds. A refusal keeps the field, so the person can fix what they typed."""
        said, target = typing.text, typing.target
        if typing.purpose == "discuss":
            item = self.record(inbox.discuss, target, said)
        elif typing.purpose == "typed":
            question = self.item(f"item:{target}")
            letter = inbox.typed_letter(question) if question is not None else None
            item = self.record(inbox.answer, target, letter, said)
        else:
            item = self.record(lambda name: inbox.start_discussion(name, said, pr=target))
        if item is None:
            return
        self.field = None
        if typing.purpose in ("pr", "session"):
            self.close_drawer()
            self.drawer = self.selected = item_key(item)
            self.settle_view()

    # Laying out the screen

    def lines(self, width: int) -> tuple[list[Line], tuple[int, int]]:
        """The screen's body, and the first and last of its lines that show the cursor's row and its drawer."""
        with self.lock:
            return self.home_lines() if self.view == "home" else self.panel_lines(width)

    def footer(self) -> list[Line]:
        """The lines under the body: what the keys do, and the last refusal or note."""
        with self.lock:
            lines = [Line([(self.status, "error")])] if self.status else []
            if self.field is not None:
                return [*lines, Line([("Enter sends · Esc cancels", "subtle")])]
            if self.view == "home":
                hint = "↑↓ move · Enter open · q quit"
            elif self.drawer is None:
                hint = "↑↓ move · Enter open · 7 discuss this session · ← or Esc back · q quit"
            else:
                hint = "Enter or Esc close · ← back · q quit"
            return [*lines, Line([(hint, "subtle")])]

    def home_lines(self) -> tuple[list[Line], tuple[int, int]]:
        now = self.now_ms()
        listed = self.home()
        lines = [Line([("Projector console", "bold"),
                       (f"  {counted(len(listed), 'live session') or 'no live sessions'}", "subtle")]), Line([])]
        if not listed:
            lines.append(Line([("No session is live. A session shows here while Claude Code's panel or the watch "
                                "daemon's Codex adapter delivers to it.", "subtle")]))
        chosen = self.selected_key()
        focus = (0, 0)
        for session in listed:
            start = len(lines)
            lines.append(Line([(session.label, "bold"), (f"  {session.host}", "subtle"),
                               *([(f"  {session.place}", "accent")] if session.place else []),
                               *([(f"  {' · '.join(counts_of(session, now))}", "")] if counts_of(session, now) else [])],
                              selected=session.name == chosen))
            if session.summary:
                lines.append(Line([(f"  {session.summary}", "subtle")]))
            named = headline(session, now)
            if named is not None:
                lines.append(Line([(f"  ‣ {named['subject']}", ""),
                                   *([("  replied", "replied")] if is_replied(named) else [])]))
            if session.name == chosen:
                focus = (start, len(lines) - 1)
        return lines, focus

    def panel_lines(self, width: int) -> tuple[list[Line], tuple[int, int]]:
        session = self.viewed()
        if session is None:
            return [], (0, 0)
        now = self.now_ms()
        counts = counts_of(session, now)
        lines = [Line([(session.label, "bold"), (f"  {session.host}", "subtle"),
                       *([(f"  {session.place}", "accent")] if session.place else []),
                       *([(f"  {' · '.join(counts)}", "subtle")] if counts else [])])]
        if session.summary:
            lines.append(Line([(session.summary, "subtle")]))
        lines.append(Line([]))
        entries = self.entries()
        if not entries:
            lines.append(Line([("No pull requests or items. Press 7 to start a discussion about this session.",
                                "subtle")]))
        chosen = self.selected_key()
        focus = (0, 0)
        label_width = max((len(row.label) for row in session.rows), default=0)
        author_width = max((len(row.pull.author or "") for row in session.rows if row.section == "review"), default=0)
        previous = None
        for key, entry in entries:
            group = entry.section if isinstance(entry, rows.Row) else "items"
            if previous is not None and group != previous:
                lines.append(Line([]))
            previous = group
            start = len(lines)
            if isinstance(entry, rows.Row):
                lines.append(self.pull_line(entry, label_width, author_width, key == chosen))
                if key == self.drawer:
                    lines += self.pull_drawer(entry)
            else:
                lines.append(self.item_line(entry, now, key == chosen))
                if key == self.drawer:
                    lines += self.item_drawer(entry, width)
            if key == chosen:
                focus = (start, len(lines) - 1)
        return lines, focus

    def pull_line(self, row: rows.Row, label_width: int, author_width: int, selected: bool) -> Line:
        pull = row.pull
        parts = [(row.label.ljust(label_width), "")]
        if pull.is_loading:
            parts.append(("  loading…", "subtle"))
            return Line(parts, selected)
        parts.append((f"  {row.state}", {"conflict": "error", "failing": "error", "changes": "warning",
                                         "findings": "warning", "clean": "success"}.get(row.state, "accent")))
        if row.section == "review":
            parts.append((f"  {(pull.author or '').ljust(author_width)}", "subtle"))
        parts.append((f"  {pull.title}", ""))
        if row.notes:
            parts.append((f"  {' · '.join(row.notes)}", "subtle"))
        return Line(parts, selected)

    def pull_drawer(self, row: rows.Row) -> list[Line]:
        pull, indent = row.pull, " " * DRAWER_INDENT
        facts = [" and ".join(row.roles)] if row.roles else []
        facts += [pull.branch] if pull.branch else []
        if not pull.is_loading:
            files = int(pull.changed_files)
            facts.append(f"+{int(pull.additions)} −{int(pull.deletions)} in {files} file{'' if files == 1 else 's'}")
        facts += [f"checks {pull.checks}"] if pull.checks else []
        facts += pull.labels
        lines = [Line([(indent + " · ".join(facts), "subtle")]), self.controls_line(self.controls())]
        if pull.open_threads and self.threads is None:
            lines.append(Line([(f"{indent}loading threads…", "subtle")]))
        for path, threads in rows.threads_by_file(self.threads or []):
            lines.append(Line([(f"{indent}{path} ", ""),
                               (f"{len(threads)} finding{'' if len(threads) == 1 else 's'}", "warning")]))
            for thread in threads:
                where = f"{thread.line}: " if thread.line is not None else ""
                said = f"{where}{thread.author or 'someone'}: {thread.excerpt}"
                lines.append(Line([(f"{indent}  {said}", "subtle" if thread.is_outdated else "")]))
        return lines

    def item_line(self, item: dict, now: int, selected: bool) -> Line:
        is_open = not item.get("answer")
        style = "subtle" if not is_open or is_snoozed(item, now) else {
            "question": "accent", "request": "warning", "notice": "", "discussion": ""}[item["kind"]]
        parts = [(f"- {item['subject']}", style)]
        parts += [(f"  {word}", mark) for word, mark in marks_of(item)]
        parts.append((f"  {age(now - item.get('postedAt', now))}", "subtle"))
        return Line(parts, selected)

    def item_drawer(self, item: dict, width: int) -> list[Line]:
        indent = " " * DRAWER_INDENT
        wrap = max(10, width - DRAWER_INDENT)
        lines: list[Line] = []
        if item.get("detail"):
            lines.append(Line([]))
            for paragraph in item["detail"].splitlines():
                lines += [Line([(indent + part, "")]) for part in textwrap.wrap(paragraph, wrap) or [""]]
        if item.get("thread"):
            lines.append(Line([]))
            for said in item["thread"]:
                who = "you: " if said.get("from") == "person" else "agent: "
                first, *rest = textwrap.wrap(said.get("text", ""), max(1, wrap - len(who))) or [""]
                lines.append(Line([(indent + who, "subtle"), (first, "")]))
                lines += [Line([(indent + " " * len(who) + part, "")]) for part in rest]
        lines += self.answer_lines(item, indent)
        stage = stage_of(item)
        if item.get("start") and not item.get("answer"):
            reason = (item.get("progress") or {}).get("reason")
            said = f"Blocked: {reason}" if stage == "blocked" else f"{(stage or 'start').capitalize()}: {item['start']}"
            lines += [Line([]), Line([(indent + said, "warning" if stage == "blocked" else "")])]
        controls = self.controls()
        options = [one for one in controls if one.option]
        if options:
            lines.append(Line([]))
            lines += [Line([(f"{indent}{one.key}: {one.label}", "accent")]) for one in options]
        rest = [one for one in controls if not one.option]
        if rest:
            lines += [Line([]), self.controls_line(rest)]
        return lines

    def answer_lines(self, item: dict, indent: str) -> list[Line]:
        """What a resolved item's drawer says became of it."""
        answered = item.get("answer")
        if not answered:
            return []
        if answered.get("withdrawn"):
            said = "Withdrawn by its agent"
        elif answered.get("declined"):
            said = "Declined"
        elif answered.get("dismissed"):
            said = "Dismissed"
        elif answered.get("done"):
            said = "Done"
        else:
            said = f"Answered: {answered.get('text', '')}"
        if answered.get("text") and (answered.get("done") or answered.get("declined")):
            said += f": {answered['text']}"
        return [Line([]), Line([(f"{indent}{said} ✓", "success")])]

    def controls_line(self, controls: list[Control]) -> Line:
        return Line([(" " * DRAWER_INDENT + "   ".join(f"{one.key}: {one.label}" for one in controls), "accent")])


# The terminal

CURSES_KEYS = {"KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_ENTER": "enter",
               "KEY_BACKSPACE": "backspace", "KEY_DC": "delete", "KEY_HOME": "home", "KEY_END": "end"}
CHARACTER_KEYS = {"\n": "enter", "\r": "enter", "\x1b": "esc", "\x7f": "backspace", "\b": "backspace",
                  "\x01": "home", "\x05": "end"}
STYLE_COLORS = {"accent": "COLOR_CYAN", "success": "COLOR_GREEN", "warning": "COLOR_YELLOW", "error": "COLOR_RED",
                "replied": "COLOR_MAGENTA"}


def key_name(pressed: object, curses: Any) -> Optional[str]:
    """The name `Console.key` takes for what `get_wch` returned."""
    if isinstance(pressed, str):
        return CHARACTER_KEYS.get(pressed, pressed)
    return next((name for constant, name in CURSES_KEYS.items() if pressed == getattr(curses, constant, None)), None)


def styles(curses: Any) -> dict[str, int]:
    """Each style's attributes: the terminal's own colors on its own background, or none under `NO_COLOR`."""
    found = {"": 0, "subtle": curses.A_DIM, "bold": curses.A_BOLD}
    colored = not os.environ.get("NO_COLOR") and curses.has_colors()
    if colored:
        curses.start_color()
        curses.use_default_colors()
    for index, (style, color) in enumerate(STYLE_COLORS.items(), 1):
        if colored:
            curses.init_pair(index, getattr(curses, color), -1)
            found[style] = curses.color_pair(index)
        else:
            found[style] = curses.A_BOLD if style in ("error", "replied") else 0
    return found


def printable(text: str) -> str:
    """Text as it can be drawn: an agent's subject or reply could hold a control character, which would move the
    terminal's cursor."""
    return "".join(character if character.isprintable() else " " for character in text)


class Screen:
    """Draws a `Console` with curses and feeds it keys."""

    def __init__(self, window: Any, console: Console, curses: Any) -> None:
        self.window, self.console, self.curses = window, console, curses
        self.styles = styles(curses)
        self.offset = 0

    def put(self, y: int, line: Line, width: int) -> None:
        x = 0
        for text, style in line.parts:
            if x >= width:
                break
            attributes = self.styles.get(style, 0) | (self.curses.A_REVERSE if line.selected else 0)
            shown = printable(text)[:width - x]
            try:
                self.window.addstr(y, x, shown, attributes)
            except self.curses.error:
                pass
            x += len(shown)

    def draw(self) -> None:
        height, width = self.window.getmaxyx()
        body, (first, last) = self.console.lines(width)
        footer = self.console.footer()
        typing = self.console.field
        room = max(1, height - len(footer) - (1 if typing else 0))
        if last - first + 1 > room:
            self.offset = first
        else:
            self.offset = min(max(self.offset, last - room + 1), first)
        self.offset = max(0, min(self.offset, max(0, len(body) - room)))
        self.window.erase()
        for y, line in enumerate(body[self.offset:self.offset + room]):
            self.put(y, line, width)
        for y, line in enumerate(footer, room):
            self.put(y, line, width)
        if typing is not None:
            space = max(1, width - len(typing.prompt) - 1)
            start = max(0, typing.cursor - space)
            self.put(height - 1, Line([(typing.prompt, "bold"), (typing.text[start:start + space], "")]), width)
            self.window.move(height - 1, min(width - 1, len(typing.prompt) + typing.cursor - start))
        try:
            self.curses.curs_set(0 if typing is None else 1)
        except self.curses.error:
            pass
        self.window.refresh()

    def run(self) -> int:
        self.window.keypad(True)
        self.window.timeout(250)
        stop = threading.Event()

        def refresh() -> None:
            while not stop.is_set():
                try:
                    self.console.tick()
                except Exception as error:  # a failed refresh must not stop the ones after it
                    self.console.status = f"refresh failed: {error}"
                self.console.refresh_now.wait(0.25)
                self.console.refresh_now.clear()

        worker = threading.Thread(target=refresh, daemon=True)
        worker.start()
        try:
            while not self.console.quit:
                self.draw()
                try:
                    pressed = self.window.get_wch()
                except self.curses.error:
                    continue
                name = key_name(pressed, self.curses)
                if name is not None:
                    self.console.key(name)
        finally:
            stop.set()
            self.console.refresh_now.set()
        return 0


def main(wake: Callable[[str, dict], None]) -> int:
    """Run the console in this terminal until `q`."""
    import curses

    os.environ.setdefault("ESCDELAY", "25")
    console = Console(watch.state_root(), wake=wake)
    try:
        return curses.wrapper(lambda window: Screen(window, console, curses).run())
    except KeyboardInterrupt:
        return 0
