"""`project console`: live Projector sessions grouped by directory, above the selected session's panel.

Enter moves the focus from the session list into its panel: its pull request rows, as `rows.py` reads them,
its inbox items, each with a drawer that answers, discusses, dismisses, or snoozes it. Every choice and message
is recorded with the functions `project inbox` runs, for the session on screen,
and the session's own deliverer brings it in: Claude Code's panel, or the watch daemon's Codex adapter. The
console never claims, delivers, or settles a line, so it never races a deliverer.

`Console` holds what is on screen, turns key names into those calls, and lays the screen out as lines of styled
text. `main` puts it in a terminal with curses, which only `main` imports, so tests drive `Console` without one.
"""

from __future__ import annotations

import base64
import os
import signal
import sys
import textwrap
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

from . import agents, artifact_panel, codex_watch, inbox, repo_inbox, rows, session_titles, summary, watch
from .core import ProjectorError

INBOX_EVERY = 1.0
ROWS_EVERY = 5.0
THREADS_EVERY = 30.0
# How long a resolved, delivered, or snoozed item stays listed, as in the panel.
RESOLVED_MS = 30_000
# The host each deliverer `watch.delivery` names belongs to.
HOSTS = {"mod": "claude", "codex": "codex"}
KINDS = ("question", "request", "notice", "message")
ASKING_KINDS = ("question", "request")
NOUNS = {"pr": "pull request", "review": "review request", **{kind: kind for kind in KINDS}}
SNOOZES = (("15 min", 15), ("1 hour", 60), ("4 hours", 240), ("1 day", 1440))
SUBSCRIPTION_ROLE = {held: role for role, held in rows.ROLE_OF.items()}
DRAWER_INDENT = 4
# Page Down or Space, Page Up or `b`, End or `G`, and Home or `g`: the second of each pair for a terminal that keeps
# the first for its own scrollback.
SCROLL_KEYS = ("pagedown", " ", "pageup", "b", "end", "G", "home", "g")
# The variables an SSH server sets in a session's environment. mosh's server starts over SSH, so its shells have
# them too.
SSH_VARIABLES = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
# How long after a panel's first Ctrl-C a second one quits, in seconds.
EXIT_WINDOW = 3.0
# What Enter on Snooze or on a pull request's Dismiss puts the focus on, and what leaving that question puts it back on.
ASKED_FOCUS = {"snooze": ("1", "9"), "dismiss": ("8", "8")}
# The color of each word after a pull request's label or title, as the panel's `HOLDS`, `REVIEW_LABELS`, `rowState`,
# and `notesOf` give them; `draft` draws in the default color.
WORD_STYLES = {"conflict": "error", "failing": "error", "changes": "warning", "findings": "warning",
               "reviewing": "claude", "clean": "success", "ready": "suggestion", "approved": "success",
               "passing": "success", "cancelled": "subtle"}
CHECK_STYLES = {"passing": "success", "failing": "error", "pending": "warning", "cancelled": "subtle"}
ROLE_STYLES = {"fixing": "fixer", "reviewing": "claude"}
STAGE_STYLES = {"starting": "subtle", "ready": "success", "blocked": "warning"}


@dataclass
class Session:
    """A live session as the console shows it."""

    name: str
    host: str
    label: str
    cwd: str = ""
    updated_at: Optional[float] = None
    signature: Optional[tuple] = None
    items: list[dict] = field(default_factory=list)
    rows: list[rows.Row] = field(default_factory=list)
    # The repository inbox's entries for the repository the session works in.
    entries: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)


@dataclass
class Field:
    """The one-line text field: what sending it records, and the words typed so far with the cursor in them. A
    `note` field records the option it opened under through `then`, and the drawer draws it under that option
    rather than at the bottom of the screen."""

    purpose: str
    target: Optional[str]
    prompt: str
    text: str = ""
    cursor: int = 0
    then: Optional[Callable[[Optional[str]], Optional[dict]]] = None

    @property
    def inline(self) -> bool:
        return self.purpose == "note"


@dataclass(frozen=True)
class Control:
    """A key a drawer offers, its label, what pressing it does, and whether it is one of the item's options, which
    the drawer lists one to a line above its other keys. A `choice` is an option opening the drawer puts the focus
    on: a question's answers and a request's outcomes, but not a request's Start or a snooze length."""

    key: str
    label: str
    act: Callable[[], None]
    option: bool = False
    choice: bool = False


@dataclass
class Line:
    """One line of the screen: its parts, each text with a style name, and whether the focus is on it: on the whole
    line for a row or an option, or on the part at `lit` for one of a line of actions. A `spot` line, the focus or
    a note field, scrolls into view when the focus moves, and `cursor` is the column of the terminal's cursor on
    the note field's line."""

    parts: list[tuple[str, str]]
    selected: bool = False
    spot: bool = False
    cursor: Optional[int] = None
    lit: Optional[int] = None

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.parts)


@dataclass
class Work:
    """What one refresh reads, decided under the lock and done outside it."""

    inbox: bool
    rows: bool
    signatures: dict[str, Optional[tuple]]
    items: dict[str, list[dict]] = field(default_factory=dict)
    directories: dict[str, str] = field(default_factory=dict)
    pull: Optional[rows.Pull] = None
    drawer: Optional[str] = None


@dataclass
class Found:
    """What a refresh read."""

    live: Optional[dict[str, tuple[str, str, str, Optional[float], tuple, Optional[list[dict]]]]] = None
    rows: dict[str, list[rows.Row]] = field(default_factory=dict)
    entries: dict[str, list[dict]] = field(default_factory=dict)
    artifacts: dict[str, list[dict]] = field(default_factory=dict)
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


def describe(root: Path, name: str, delivery: str, record: dict) -> tuple[str, str, str, Optional[float]]:
    """The session's display name, host, directory, and registration's last update, in milliseconds."""
    host = HOSTS.get(delivery) or record.get("host") or "other"
    label = record.get("name") or "Unnamed session"
    cwd = codex_watch.registration(watch.session_folder(root, name)).get("cwd") or record.get("cwd")
    changed = watch.seconds_of(record.get("updated_at"))
    return label, host, cwd if isinstance(cwd, str) else "", changed * 1000 if changed is not None else None


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
    owes = inbox.owed_message(item) is not None
    marks = []
    if (not is_open and not item.get("deliveredAt")) or owes:
        marks.append(("sending", "subtle"))
    if is_open and item.get("unclear"):
        marks.append(("unclear", "warning"))
    stage = stage_of(item)
    if stage is not None:
        marks.append((stage, STAGE_STYLES[stage]))
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
    parts.append(counted(len(session.artifacts), 'artifact'))
    parts += [counted(sum(1 for item in awake if item["kind"] == kind), NOUNS[kind]) for kind in KINDS]
    return [part for part in parts if part is not None]


def needs(session: Session, now_ms: int) -> tuple:
    """The home list's order: the most open questions and requests first, then the newest item, then the
    label."""
    shown = shown_items(session, now_ms)
    asking = sum(1 for item in shown if is_awake(item, now_ms) and item["kind"] in ASKING_KINDS)
    newest = max((item.get("postedAt", 0) for item in shown), default=0)
    return (-asking, -newest, session.label, session.name)


def headline(session: Session, now_ms: int) -> Optional[dict]:
    """The newest open, awake question or request."""
    return next((item for item in shown_items(session, now_ms)
                 if item["kind"] in ASKING_KINDS and is_awake(item, now_ms)), None)


def directory_label(cwd: str) -> str:
    if not cwd:
        return "Unknown working directory"
    try:
        relative = Path(cwd).relative_to(Path.home())
    except ValueError:
        return cwd
    return "~" if relative == Path(".") else "~/" + str(relative)


def fit(text: str, width: int) -> str:
    text = printable(text)
    if len(text) > width:
        return text[:max(0, width - 1)] + ("…" if width else "")
    return text.ljust(width)


def last_update(session: Session) -> Optional[float]:
    times = [session.updated_at, *(row.pull.updated_at for row in session.rows)]
    times += [modified / 1_000_000 for _, _, modified in session.signature or ()]
    times += [entry.get(key) for entry in session.entries for key in ("sent_at", "updated_at")]
    return max((changed for changed in times if isinstance(changed, (int, float))), default=None)


def is_remote(environ: Optional[dict] = None) -> bool:
    """Whether the console runs on a machine other than the one the person sits at, where a browser it opened would
    open out of their sight: `PROJECTOR_REMOTE=1` says it does and `PROJECTOR_REMOTE=0` that it does not, for a
    tmux server whose environment lost the SSH variables, and otherwise any of `SSH_VARIABLES` says it does."""
    environ = os.environ if environ is None else environ
    forced = environ.get("PROJECTOR_REMOTE")
    if forced in ("0", "1"):
        return forced == "1"
    return any(environ.get(name) for name in SSH_VARIABLES)


def opens_browser(environ: Optional[dict] = None) -> bool:
    """Whether `o` and `p` call the browser: on a local machine, and on a remote one whose `BROWSER` names a command,
    which `webbrowser` runs, such as a helper that opens the URL on the person's own machine through an SSH tunnel.
    A remote console without one shows the URL and sends it to the clipboard instead."""
    environ = os.environ if environ is None else environ
    return not is_remote(environ) or bool(environ.get("BROWSER"))


def clipboard_sequence(text: str, environ: Optional[dict] = None) -> str:
    """The OSC 52 escape sequence that asks the terminal to put `text` on the clipboard of the machine the person sits
    at. Under tmux, which `TMUX` names, it is wrapped in tmux's passthrough, each Esc doubled, so tmux hands it on to
    the terminal. Nothing comes back to say whether the terminal allowed it."""
    environ = os.environ if environ is None else environ
    sequence = f"\x1b]52;c;{base64.b64encode(text.encode('utf-8')).decode('ascii')}\x07"
    if environ.get("TMUX"):
        return "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    return sequence


def pull_key(pull: rows.Pull) -> str:
    return f"pr:{pull.repo}#{pull.number}"


def entry_key(entry: dict) -> str:
    return f"repo:{entry['ref']}"


def item_key(item: dict) -> str:
    return f"item:{item['ref']}"


def read_artifacts(state: Path, session: str, cwd: str) -> Optional[list[dict]]:
    try:
        checkout = summary.checkout_root(Path(cwd)) if cwd else None
        return artifact_panel.listing(checkout, 'origin', session, state) if checkout else []
    except (ProjectorError, OSError):
        return None


class Console:
    """What the console shows, and what each key does to it."""

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time,
                 gh: Callable[[list[str]], watch.GhResult] = watch.run_gh,
                 open_url: Callable[[str], Any] = webbrowser.open,
                 wake: Callable[[str, dict], None] = lambda session, item: None,
                 ensure: Callable[[], object] = lambda: None) -> None:
        self.root = root
        self.ensure = ensure
        self.clock = clock
        self.gh = gh
        self.open_url = open_url
        self.wake = wake
        self.lock = threading.RLock()
        self.refresh_now = threading.Event()
        self.sessions: dict[str, Session] = {}
        self.titles = session_titles.SessionTitles()
        self.view = "home"
        self.session: Optional[str] = None
        self.selected: Optional[str] = None
        self.drawer: Optional[str] = None
        self.asking: Optional[str] = None
        self.field: Optional[Field] = None
        self.threads: Optional[list[rows.Thread]] = None
        # The key of the open drawer's control the focus is on, or None while it is on a row, and whether the next
        # layout scrolls the focus into view.
        self.control: Optional[str] = None
        self.reveal = False
        # When a panel's first Ctrl-C asked for a second, by `clock`.
        self.exit_at: Optional[float] = None
        # The open drawer's key, the action's key, the URL that action showed rather than opened, until the next key,
        # and whether it was sent to the clipboard; and the text `Screen` still has to send there.
        self.link: Optional[tuple[str, str, str, bool]] = None
        self.clipboard: Optional[str] = None
        # The last refusal `record` showed, and the session, item, and note of a note field a revision closed, which
        # choosing again on that item of that session brings back until the person leaves its panel. Item numbers
        # repeat across sessions, so the session is part of the key.
        self.refusal: Optional[ProjectorError] = None
        self.kept_note: Optional[tuple[str, str, str]] = None
        self.offset = 0
        self.home_offset = 1
        self.home_room = 1
        self.room = 1
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
                    signatures={name: session.signature for name, session in self.sessions.items()},
                    items={name: session.items for name, session in self.sessions.items()},
                    directories={name: session.cwd for name, session in self.sessions.items()})
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
                record = agents.record(self.root, name) or {}
                entries = repo_inbox.repo_items(self.root, name, everything=True)
                current_items = work.items[name] if items is None else items
                now_ms = int(now * 1000)
                if not (record or any(not line.excluded for line in watch.subscriptions(
                        watch.session_folder(self.root, name)))
                        or any(is_shown(item, now_ms) for item in current_items)
                        or any(not repo_inbox.is_snoozed(entry, now_ms) for entry in entries)):
                    continue
                found.live[name] = (*describe(self.root, name, delivery, record), seen, items)
                found.entries[name] = entries
            titles = self.titles.read({name: details[1] for name, details in found.live.items()})
            for name, title in titles.items():
                found.live[name] = (title, *found.live[name][1:])
            names = list(found.live)
        for name in names:
            if work.rows or name not in work.signatures:
                found.rows[name] = rows.rows_for(self.root, name)
                cwd = found.live[name][2] if found.live is not None else work.directories.get(name, '')
                artifacts = read_artifacts(self.root, name, cwd)
                if artifacts is not None:
                    found.artifacts[name] = artifacts
        if work.pull is not None:
            found.threads = rows.fetch_threads(work.pull, self.gh)
        return found

    def apply(self, found: Found) -> None:
        if found.live is not None:
            for name in set(self.sessions) - set(found.live):
                del self.sessions[name]
            for name, (label, host, cwd, changed, seen, items) in found.live.items():
                session = self.sessions.setdefault(name, Session(name, host, label))
                session.host, session.label, session.cwd, session.updated_at = host, label, cwd, changed
                session.signature = seen
                if items is not None:
                    session.items = items
        for name, found_rows in found.rows.items():
            if name in self.sessions:
                self.sessions[name].rows = found_rows
        for name, entries in found.entries.items():
            if name in self.sessions:
                self.sessions[name].entries = entries
        for name, artifacts in found.artifacts.items():
            if name in self.sessions:
                hidden = artifact_panel.dismissals(name, self.root)
                self.sessions[name].artifacts = [row for row in artifacts if row['key'] not in hidden]
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
            session.entries = repo_inbox.repo_items(self.root, session.name, everything=True)
        self.settle_view()

    def settle_view(self) -> None:
        """Leave a panel whose session is gone, close a drawer whose row is gone, and close a note field whose
        option is gone."""
        if self.view == "panel" and self.session not in self.sessions:
            self.status = "The selected session is no longer live"
            self.go_home()
            return
        if self.view != "panel":
            self.selected = self.selected_key()
            return
        keys = [key for key, _ in self.entries()]
        if self.drawer is not None and self.drawer not in keys:
            self.close_drawer()
        if self.field is not None and self.field.inline and not self.choices():
            self.field = None
        if self.control is not None and self.control not in {one.key for one in self.controls()}:
            self.control = None

    # What is on screen

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def home(self) -> list[Session]:
        now = self.now_ms()
        return sorted(self.sessions.values(), key=lambda session: (not bool(session.cwd), session.cwd,
                                                                  needs(session, now)))

    def viewed(self) -> Optional[Session]:
        name = self.selected if self.view == "home" else self.session
        if self.view == "home" and name not in self.sessions:
            return next(iter(self.home()), None)
        return self.sessions.get(name or "")

    def entries(self) -> list[tuple[str, object]]:
        """The viewed panel's rows: its pull requests, then its items, then its repository inbox's entries, each under
        its key."""
        session = self.viewed()
        if session is None:
            return []
        now = self.now_ms()
        return ([(pull_key(row.pull), row) for row in session.rows]
                + [(f"artifact:{entry['key']}", entry) for entry in session.artifacts]
                + [(item_key(item), item) for item in shown_items(session, now)]
                + [(entry_key(entry), entry) for entry in session.entries if not repo_inbox.is_snoozed(entry, now)])

    def keys(self) -> list[str]:
        if self.view == "home":
            return [session.name for session in self.home()]
        return [key for key, _ in self.entries()]

    def row(self, key: Optional[str]) -> Optional[rows.Row]:
        found = dict(self.entries()).get(key) if key and key.startswith("pr:") else None
        return found if isinstance(found, rows.Row) else None

    def entry(self, key: Optional[str]) -> Optional[dict]:
        found = dict(self.entries()).get(key) if key and key.startswith("repo:") else None
        return found if isinstance(found, dict) else None

    def item(self, key: Optional[str]) -> Optional[dict]:
        found = dict(self.entries()).get(key) if key and key.startswith("item:") else None
        return found if isinstance(found, dict) else None

    # Keys

    def key(self, name: str) -> None:
        """Do what the key named `name` does: a character, or `up`, `down`, `left`, `right`, `enter`, `esc`,
        `backspace`, `delete`, `home`, `end`, `pageup`, `pagedown`, or `ctrl-c`."""
        with self.lock:
            self.status = ""
            self.link = None
            if name == "ctrl-c" and self.field is None:
                self.interrupt()
                return
            self.exit_at = None
            if self.field is not None:
                self.field_key(name)
            elif name in ("up", "k", "down", "j"):
                step = -1 if name in ("up", "k") else 1
                if self.view == "panel":
                    self.step_focus(step)
                else:
                    self.move(step)
            elif name in SCROLL_KEYS:
                self.scroll(name)
            elif self.view == "home":
                if name in ("enter", "right") and self.selected_key() is not None:
                    self.open_panel(self.selected_key())
            else:
                self.panel_key(name)

    def scroll(self, name: str) -> None:
        """Page between sessions in the list, or scroll a panel's open drawer."""
        if self.view == "home":
            keys = self.keys()
            if keys:
                if name in ("end", "G", "home", "g"):
                    self.selected = keys[-1] if name in ("end", "G") else keys[0]
                    self.offset = 0
                else:
                    self.move(max(1, self.home_room - 2) * (1 if name in ("pagedown", " ") else -1))
            return
        page = max(1, self.room - 1)
        if name in ("pagedown", " "):
            self.offset += page
        elif name in ("pageup", "b"):
            self.offset -= page
        elif name in ("end", "G"):
            self.offset = sys.maxsize
        else:
            self.offset = 0

    def selected_key(self) -> Optional[str]:
        keys = self.keys()
        return self.selected if self.selected in keys else (keys[0] if keys else None)

    def interrupt(self) -> None:
        """Ctrl-C: quit from the home list, and from a panel on the second press within `EXIT_WINDOW` seconds."""
        now = self.clock()
        if self.view == "home" or (self.exit_at is not None and now - self.exit_at < EXIT_WINDOW):
            self.quit = True
        else:
            self.exit_at = now

    def move(self, step: int) -> None:
        """Move the cursor a session up or down the home list, staying put at either end."""
        keys = self.keys()
        if not keys:
            return
        at = keys.index(self.selected) if self.selected in keys else 0
        self.selected = keys[max(0, min(len(keys) - 1, at + step))]
        self.offset = 0

    def choices(self) -> list[Control]:
        """The open drawer's choices, which the note field opens under."""
        return [one for one in self.controls() if one.choice]

    def ring(self) -> list[tuple[str, Optional[str]]]:
        """Every place the panel's focus can be, in order: each row, as its key and None, with the open drawer's
        controls, as its key and each control's, directly under its row."""
        found: list[tuple[str, Optional[str]]] = []
        for key, _ in self.entries():
            found.append((key, None))
            if key == self.drawer:
                found += [(key, one.key) for one in self.controls()]
        return found

    def focus(self) -> tuple[Optional[str], Optional[str]]:
        """Where the panel's focus is: a row's key, and the key of the open drawer's control it is on, if any."""
        key = self.selected_key()
        return key, self.control if key is not None and key == self.drawer else None

    def step_focus(self, step: int) -> None:
        """Move the focus a place up or down the ring, staying put at either end. The open drawer stays open."""
        ring = self.ring()
        if not ring:
            return
        at = ring.index(self.focus()) if self.focus() in ring else 0
        self.selected, self.control = ring[max(0, min(len(ring) - 1, at + step))]
        self.reveal = True

    def open_panel(self, name: str) -> None:
        self.view, self.session, self.selected = "panel", name, None
        self.offset = 0
        self.close_drawer()

    def go_home(self) -> None:
        self.close_drawer()
        self.field = self.kept_note = None
        self.view, self.selected, self.session = "home", self.session, None
        self.offset = 0

    def close_drawer(self) -> None:
        """Close the open drawer. A focus on one of its controls goes to its row, where `selected` already is."""
        self.drawer = self.asking = None
        self.threads = None
        self.control = None

    def panel_key(self, name: str) -> None:
        if name in ("left", "esc", "0"):
            if self.drawer is None:
                self.go_home()
            else:
                self.selected = self.drawer
                self.close_drawer()
        elif name == "enter":
            self.press_focus()
        elif self.drawer is not None:
            control = next((one for one in self.controls() if one.key == name), None)
            if control is not None:
                self.press(control)
        elif name == "7":
            self.open_field("session", None, "Message this session: ")

    def press_focus(self) -> None:
        """Enter: press the control the focus is on, close the drawer of the open row, open another row's."""
        key, control = self.focus()
        if key is None:
            return
        found = next((one for one in self.controls() if one.key == control), None)
        if found is not None:
            self.press(found)
        elif key == self.drawer:
            self.close_drawer()
        else:
            self.open_drawer(key)

    def press(self, control: Control) -> None:
        """Press one of the open drawer's controls, from Enter or its own key, moving the focus onto it first."""
        self.selected, self.control = self.drawer, control.key
        control.act()

    def open_drawer(self, key: str) -> None:
        """Open the row's drawer in place of the open one, with the focus on its first choice, or else on its row."""
        self.close_drawer()
        self.drawer = self.selected = key
        choices = self.choices()
        self.control = choices[0].key if choices else None
        if key.startswith("pr:"):
            self.next_threads = float("-inf")
            self.refresh_now.set()
        self.settle_view()

    def controls(self) -> list[Control]:
        if self.drawer and self.drawer.startswith('artifact:'):
            row = dict(self.entries()).get(self.drawer)
            if row is not None:
                return [Control('o', 'Open', lambda: self.open_artifact(row)),
                        Control('8', 'Dismiss', lambda: self.dismiss_artifact(row))]
        row, item, entry = self.row(self.drawer), self.item(self.drawer), self.entry(self.drawer)
        if row is not None:
            return self.pull_controls(row)
        if entry is not None:
            return self.entry_controls(entry)
        return self.item_controls(item) if item is not None else []

    def open_artifact(self, row: dict) -> None:
        try:
            self.visit('o', artifact_panel.open_url(Path(row['checkout']), row['remote'], row['id']))
        except (ProjectorError, OSError) as error:
            self.status = str(error)

    def dismiss_artifact(self, row: dict) -> None:
        try:
            artifact_panel.dismiss(Path(row['checkout']), row['remote'], self.session, row['id'], state=self.root)
            session = self.viewed()
            if session is not None:
                session.artifacts = [item for item in session.artifacts if item['key'] != row['key']]
            self.close_drawer()
            self.settle_view()
        except ProjectorError as error:
            self.status = str(error)

    def entry_controls(self, entry: dict) -> list[Control]:
        """A repository inbox entry's keys. A peer's question takes its answers, and a request Done or its options
        and Decline, each recorded at once as the user's and sealed back to the peer; no line goes to the session,
        whose agent did not ask. An item you sent can be closed or reopened, and any entry dismissed, and snoozed
        unless it is Projector's own local notice, which takes a dismissal and nothing else."""
        ref = entry["ref"]
        if self.asking == "snooze":
            return [*(Control(str(index), label, lambda minutes=minutes: self.change_entry(ref, "snooze", minutes),
                              True) for index, (label, minutes) in enumerate(SNOOZES, 1)),
                    Control("9", "Cancel", self.stop_asking)]
        controls = []
        if entry["role"] == "incoming":
            if entry["kind"] == "question":
                controls += [Control(str(at + 1), f"{choice}{' (recommended)' if at == 0 else ''}",
                                     lambda letter=chr(ord("a") + at): self.respond(ref, "answer", letter=letter),
                                     True, True) for at, choice in enumerate(entry["choices"])]
            elif entry["kind"] == "request":
                given = entry.get("choices")
                controls += [Control(str(at + 1), choice,
                                     lambda choice=choice: self.respond(ref, "done", text=choice if given else None),
                                     True, True) for at, choice in enumerate(given or ["Done"])]
                controls.append(Control("6", "Decline", lambda: self.respond(ref, "declined")))
            controls += [Control("7", "Discuss", lambda: self.open_field("discuss-entry", ref, f"Discuss {ref}: ")),
                         Control("8", "Dismiss", lambda: self.respond(ref, "dismissed"))]
        else:
            if entry["role"] == "sent":
                controls.append(Control("6", "Reopen", lambda: self.change_entry(ref, "reopen"))
                                if repo_inbox.is_closed(entry) else
                                Control("6", "Close", lambda: self.change_entry(ref, "close")))
            controls.append(Control("8", "Dismiss", lambda: self.change_entry(ref, "dismiss")))
        if entry["role"] != repo_inbox.LOCAL:
            controls.append(Control("9", "Snooze", lambda: self.ask("snooze")))
        return controls

    def respond(self, ref: str, outcome: str, *, letter: Optional[str] = None, text: Optional[str] = None) -> None:
        self.change(lambda name: repo_inbox.respond_for(self.root, name, ref, outcome, letter=letter, text=text,
                                                        now=self.clock()))

    def change_entry(self, ref: str, command: str, minutes: int = 0) -> None:
        self.change(lambda name: repo_inbox.change_for(self.root, name, ref, command, self.clock(), minutes))

    def change(self, act: Callable[[str], dict]) -> None:
        """Change a repository inbox entry for the viewed session, then start the watch daemon, which pushes it."""
        session = self.viewed()
        if session is None:
            return
        try:
            act(session.name)
        except ProjectorError as error:
            self.status = str(error)
            return
        self.stop_asking()
        try:
            self.ensure()
        except (ProjectorError, OSError) as error:
            self.status = f"recorded, but the watch daemon did not start: {error}"
        self.reload()

    def pull_controls(self, row: rows.Row) -> list[Control]:
        pull = row.pull
        if self.asking == "dismiss":
            return [Control("8", "Yes, dismiss it", lambda: self.dismiss_pull(row)),
                    Control("9", "No, keep it", self.stop_asking)]
        reference = f"{pull.repo}#{pull.number}"
        controls = [Control("7", "Discuss", lambda: self.open_field("pr", reference, f"Discuss {reference}: ")),
                    Control("8", "Dismiss", lambda: self.ask("dismiss")),
                    Control("o", "Open in GitHub", lambda: self.visit("o", pull.url))]
        preview = rows.cached_preview(self.root, pull.repo, pull.number)
        if preview is not None:
            controls.append(Control("d", "Open in Vercel", lambda: self.visit("d", preview)))
        if pull.summary_url is not None:
            summary = pull.summary_url
            controls.append(Control("p", "Open in Projector", lambda: self.visit("p", summary)))
        return controls

    def visit(self, key: str, url: str) -> None:
        """Open `url` in a browser when `opens_browser` says to, and otherwise, or when nothing opened, show it under
        the action at `key` for the person's terminal to make clickable. A remote machine also sends it to the
        person's clipboard."""
        remote = is_remote()
        if opens_browser():
            try:
                if self.open_url(url) is not False:
                    return
            except (webbrowser.Error, OSError):
                pass
        if remote:
            self.clipboard = url
        if self.drawer is not None:
            self.link = (self.drawer, key, url, remote)

    def take_clipboard(self) -> Optional[str]:
        """The text an action asked to send to the clipboard, once."""
        with self.lock:
            text, self.clipboard = self.clipboard, None
            return text

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
        # The item as the drawer shows it, which a choice made through the note field must still match when it is
        # recorded.
        read, options = item["subject"], list(item.get("choices") or [])
        if item["kind"] == "question":
            letters = [chr(ord("a") + index) for index in range(len(item["choices"]))]
            controls += [Control(str(at + 1), f"{choice}{' (recommended)' if at == 0 else ''}",
                                 lambda letter=letter: self.choose(ref, lambda note: self.record(
                                     lambda name: inbox.answer(name, ref, letter, None, note, subject=read,
                                                               choices=options))),
                                 True, True)
                         for at, (choice, letter) in enumerate(zip(item["choices"], letters))]
        elif item["kind"] == "request":
            start = start_of(item)
            if start is not None:
                controls.append(Control("1", start, lambda: self.record(inbox.start, ref, item["start"]), True))
            given = item.get("choices")
            controls += [Control(str(at + (1 if start is None else 2)), choice,
                                 lambda choice=choice: self.choose(ref, lambda note: self.record(
                                     lambda name: inbox.done(name, ref, choice if given else None, note, subject=read,
                                                             choices=options))),
                                 True, True)
                         for at, choice in enumerate(given or ["Done"])]
        if not item.get("unclear") and item["kind"] != "message":
            controls.append(Control("6", "I don't understand",
                                    lambda: self.record(inbox.unclear, ref, item["subject"], item.get("detail", ""))))
        controls += [Control("7", "Discuss", lambda: self.open_field("discuss", ref, f"Discuss {ref}: ")),
                     Control("8", "Dismiss", lambda: self.record(inbox.dismiss, ref)),
                     Control("9", "Unsnooze", lambda: self.record(inbox.restore, ref)) if is_snoozed(item, now)
                     else Control("9", "Snooze", lambda: self.ask("snooze"))]
        return controls

    def choose(self, ref: str, then: Callable[[Optional[str]], Optional[dict]]) -> None:
        """Pick the focused choice: open the note field under it, whose Enter records it, holding the note a
        revision of the item made the person leave."""
        self.reveal = True
        kept = self.kept_note[2] if self.kept_note is not None and self.kept_note[:2] == (self.session, ref) else ""
        self.kept_note = None
        self.field = Field("note", ref, "Note (optional): ", text=kept, cursor=len(kept), then=then)

    def ask(self, what: str) -> None:
        """Ask a follow-up question, the snooze lengths or a pull request's confirmation, with the focus on it."""
        self.asking, self.control = what, ASKED_FOCUS[what][0]

    def stop_asking(self) -> None:
        """Put away the follow-up question, and the focus back on the control that asked it."""
        if self.asking is not None:
            self.control = ASKED_FOCUS[self.asking][1]
        self.asking = None

    # Recording

    def record(self, change: Callable[..., dict], *args) -> Optional[dict]:
        """Record a choice with an inbox function for the viewed session, then show it. For a Codex session,
        wake the daemon, which delivers its lines."""
        session = self.viewed()
        if session is None:
            return None
        self.refusal = None
        try:
            item = change(session.name, *args)
        except ProjectorError as error:
            self.status, self.refusal = str(error), error
            return None
        self.stop_asking()
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
        self.stop_asking()
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
        if name in ("esc", "ctrl-c"):
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
        if typing.purpose == "note" and typing.then is not None:
            item = typing.then(said.strip() or None)
            if (item is None and isinstance(self.refusal, inbox.ChangedError) and target is not None
                    and self.session is not None):
                # The choice may no longer name what the person read, so the field closes on the revised item,
                # keeping their note for the choice they make next.
                self.field, self.kept_note = None, (self.session, target, said)
                self.reload()
                return
        elif typing.purpose == "discuss":
            item = self.record(inbox.discuss, target, said)
        elif typing.purpose == "discuss-entry":
            # A peer's entry has no item in the session's own inbox to hang the message on, so it becomes a
            # message about the session that quotes the entry.
            entry = self.entry(f"repo:{target}")
            quoted = f'[{target}] "{entry["subject"]}" from {repo_inbox.peer_label(entry)}: ' if entry else ""
            item = self.record(lambda name: inbox.send(name, quoted + said))
        else:
            item = self.record(lambda name: inbox.send(name, said, pr=target))
        if item is None:
            return
        self.field = None
        if typing.purpose in ("pr", "session"):
            # The message is an item of its own, which reads `sending` until it is delivered and then leaves.
            self.close_drawer()
            self.selected = item_key(item)

    # Laying out the screen

    def lines(self, width: int) -> tuple[list[Line], tuple[int, int]]:
        """Both panes, and the range containing the focused row and its drawer."""
        with self.lock:
            home, home_focus = self.home_lines(width)
            if self.viewed() is None:
                return home, home_focus
            panel, panel_focus = self.panel_lines(width)
            start = len(home) + 1
            focus = home_focus if self.view == "home" else tuple(start + at for at in panel_focus)
            return [*home, Line([("─" * max(0, width), "subtle")]), *panel], focus

    def viewport(self, width: int, room: int) -> list[Line]:
        """Keep the session list above the selected session's independently scrolling panel."""
        with self.lock:
            home, focus = self.home_lines(width)
            if self.viewed() is None:
                return home[:room]
            self.home_room = min(len(home), max(1, room // 2))
            listed = self.home_viewport(home, focus, self.home_room)
            if room < 3:
                return listed[:room]
            panel = self.panel_viewport(width, room - len(listed) - 1)
            return [*listed, Line([("─" * max(0, width), "subtle")]), *panel]

    def home_viewport(self, lines: list[Line], focus: tuple[int, int], room: int) -> list[Line]:
        """Keep the selected session and its directory heading visible as the list scrolls."""
        if len(lines) <= room:
            return lines
        first, last = focus
        if room == 1:
            return [lines[last]]
        if room == 2:
            return [lines[0], lines[last]]
        prefix = lines[:2] if room > 3 else lines[:1]
        space = room - len(prefix)
        if last - first + 1 > space:
            self.home_offset = min(max(self.home_offset, last - space + 2), last)
            return [*prefix, lines[first], *lines[self.home_offset:self.home_offset + space - 1]]
        self.home_offset = first + 1
        return [*prefix, *lines[first:first + space]]

    def panel_viewport(self, width: int, room: int) -> list[Line]:
        """The body's lines that fit in `room` rows. The offset survives each redraw and moves only as far as it
        must to show the cursor's row and its drawer. A drawer taller than the room shows a room's worth of itself,
        starting at its row, and the scroll keys move the offset through the rest."""
        with self.lock:
            body, (first, last) = self.panel_lines(width)
            self.room = room
            spots = [index for index, line in enumerate(body) if line.spot]

            def clamped(offset: int) -> int:
                if last - first + 1 > room:
                    offset = min(max(offset, first), last - room + 1)
                else:
                    offset = min(max(offset, last - room + 1), first)
                if self.reveal and spots:
                    offset = min(max(offset, spots[-1] - room + 1), spots[0])
                return max(0, min(offset, len(body) - room))

            self.offset = clamped(self.offset)
            self.reveal = False
            return body[self.offset:self.offset + room]

    def footer(self) -> list[Line]:
        """The lines under the body: what the keys do, and the last refusal or note."""
        with self.lock:
            lines = [Line([(self.status, "error")])] if self.status else []
            if self.field is not None and self.field.inline:
                return [*lines, Line([("Enter records, with the note if you typed one · Esc back to the options",
                                       "subtle")])]
            if self.field is not None:
                return [*lines, Line([("Enter sends · Esc cancels", "subtle")])]
            if self.exit_at is not None and self.clock() - self.exit_at < EXIT_WINDOW:
                return [*lines, Line([("Press Ctrl-C again to exit", "warning")])]
            if self.view == "home":
                hint = "↑↓ session · Enter or → focus panel · PgUp PgDn scroll · Ctrl-C quit"
            elif self.drawer is None:
                hint = ("↑↓ move · Enter open · 7 message this session · ← or Esc back"
                        " · Ctrl-C twice quit")
            else:
                hint = "↑↓ move · Enter press or open · ← or Esc close · PgUp PgDn scroll · Ctrl-C twice quit"
            return [*lines, Line([(hint, "subtle")])]

    def home_lines(self, width: int) -> tuple[list[Line], tuple[int, int]]:
        now = self.now_ms()
        listed = self.home()
        lines = [Line([("Projector console", "bold"),
                       (f"  {counted(len(listed), 'live session') or 'no live sessions'}", "subtle")])]
        if not listed:
            lines.append(Line([("No live Projector sessions. Sessions appear when they register an agent, watch "
                                "pull requests, or have inbox items.", "subtle")]))
            return lines, (0, 0)
        ages = {session.name: age(now - changed) if (changed := last_update(session)) is not None else "—"
                for session in listed}
        counts = {session.name: counted(len(session.rows), "PR") or "0 PRs" for session in listed}
        count_width = max(len(value) for value in counts.values())
        age_width = max(len(value) for value in ages.values())
        name_width = min(max(5, *(len(session.label) for session in listed)), max(1, width // 4),
                         max(1, width - count_width - age_width - 9))
        request_width = max(0, width - 2 - name_width - count_width - age_width - 7)

        def columns(label: str, count: str, request: str, changed: str, chosen: bool = False) -> Line:
            if width < 20:
                return Line([("▸ " if chosen else "  ", "subtle"),
                             (fit(label, max(0, width - 2)), "suggestion" if chosen else "")])
            return Line([("▸ " if chosen else "  ", "subtle"),
                         (fit(label, name_width), "suggestion" if chosen else ""),
                         (f"  {count.ljust(count_width)}  ", "claude"), (fit(request, request_width), ""),
                         (f"  {changed.rjust(age_width)} ", "subtle")])

        viewed = self.viewed()
        chosen = viewed.name if viewed is not None else None
        focus = (0, 0)
        previous: Optional[str] = None
        group_start = 0
        for session in listed:
            if session.cwd != previous:
                lines.append(Line([]))
                group_start = len(lines)
                lines.append(Line([(directory_label(session.cwd), "branch")]))
                previous = session.cwd
            start = len(lines)
            request = headline(session, now)
            lines.append(columns(session.label, counts[session.name], request["subject"] if request else "—",
                                 ages[session.name], session.name == chosen))
            if session.name == chosen:
                focus = (group_start, start)
        return lines, focus

    def panel_lines(self, width: int) -> tuple[list[Line], tuple[int, int]]:
        session = self.viewed()
        if session is None:
            return [], (0, 0)
        now = self.now_ms()
        counts = counts_of(session, now)
        lines = [Line([(session.label, ""), *([(f"  {' · '.join(counts)}", "subtle")] if counts else [])]), Line([])]
        entries = self.entries()
        if not entries:
            lines.append(Line([("No pull requests or items. Press 7 to send this session a message.", "subtle")]))
        chosen, control = self.focus() if self.view == "panel" else (None, None)
        focus = (0, 0)
        label_width = max((len(row.label) for row in session.rows), default=0)
        author_width = max((len(row.pull.author or "") for row in session.rows if row.section == "review"), default=0)
        state_width = max((len(row.state) for row in session.rows if not row.pull.is_loading), default=0)
        previous = None
        for key, entry in entries:
            group = (entry.section if isinstance(entry, rows.Row) else "artifact" if key.startswith('artifact:')
                     else "repo" if key.startswith("repo:") else "items")
            if previous is not None and group != previous:
                lines.append(Line([]))
            if group == 'artifact' and previous != group:
                lines.append(Line([('Artifacts', '')]))
            previous = group
            start = len(lines)
            # The row is lit only while the focus is on the row itself, not on one of its drawer's controls.
            lit = key == chosen and control is None
            if isinstance(entry, rows.Row):
                lines.append(self.pull_line(entry, label_width, state_width, author_width, lit))
                if key == self.drawer:
                    lines += self.pull_drawer(entry, width)
            elif key.startswith('artifact:'):
                suffix = ' · local preview' if entry['preview'] else ''
                lines.append(Line([(f"- {entry['title']}{suffix}", '')], lit))
                if key == self.drawer:
                    lines.append(Line([(' ' * DRAWER_INDENT + entry['description'], 'subtle')]))
                    lines.append(self.controls_line(self.controls()))
                    lines += self.link_lines(width)
            elif key.startswith("repo:"):
                lines.append(self.entry_line(entry, now, lit))
                if key == self.drawer:
                    lines += self.entry_drawer(entry, session.entries, width)
            else:
                lines.append(self.item_line(entry, now, lit))
                if key == self.drawer:
                    lines += self.item_drawer(entry, width)
            if key == chosen:
                focus = (start, len(lines) - 1)
        return lines, focus

    def pull_line(self, row: rows.Row, label_width: int, state_width: int, author_width: int, selected: bool) -> Line:
        pull = row.pull
        state = "" if pull.is_loading else row.state
        parts = [(row.label.ljust(label_width), ""), ("  " + state.ljust(state_width), WORD_STYLES.get(state, ""))]
        if pull.is_loading:
            parts.append(("  loading…", "subtle"))
            return Line(parts, selected)
        if row.section == "review":
            parts.append((f"  {(pull.author or '').ljust(author_width)}", "subtle"))
        parts.append((f"  {pull.title}", ""))
        for at, note in enumerate(row.notes):
            parts += [("  " if at == 0 else " · ", ""), (note, WORD_STYLES.get(note, ""))]
        return Line(parts, selected)

    def pull_drawer(self, row: rows.Row, width: int) -> list[Line]:
        """The facts line, in the panel's colors for the roles, the branch, the size, and the checks, then the
        actions, the URL an action showed rather than opened, and the threads by file."""
        pull, indent = row.pull, " " * DRAWER_INDENT
        facts: list[list[tuple[str, str]]] = []
        if row.roles:
            roles: list[tuple[str, str]] = []
            for at, role in enumerate(row.roles):
                roles += [(" and ", "")] if at else []
                roles.append((role, ROLE_STYLES.get(role, "")))
            facts.append(roles)
        facts += [[(pull.branch, "branch")]] if pull.branch else []
        if not pull.is_loading:
            files = int(pull.changed_files)
            facts.append([(f"+{int(pull.additions)}", "success"), (" ", ""), (f"−{int(pull.deletions)}", "error"),
                          (f" in {files} file{'' if files == 1 else 's'}", "")])
        facts += [[(f"checks {pull.checks}", CHECK_STYLES.get(pull.checks, ""))]] if pull.checks else []
        facts += [[(label, "")] for label in pull.labels]
        said: list[tuple[str, str]] = [(indent, "")]
        for at, fact in enumerate(facts):
            said += [(" · ", "subtle")] if at else []
            said += fact
        lines = [Line(said), self.controls_line(self.controls())]
        lines += self.link_lines(width)
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

    def link_lines(self, width: int) -> list[Line]:
        if self.link is None or self.link[0] != self.drawer:
            return []
        # The whole URL from the first column, a window's width to a line, so a terminal can find it to click.
        _, _, url, copied = self.link
        lines = [Line([(url[start:start + width], "")]) for start in range(0, len(url), max(1, width))]
        if copied:
            lines.append(Line([(" " * DRAWER_INDENT + "Sent to your clipboard where your terminal allows it",
                                "subtle")]))
        return lines

    def item_line(self, item: dict, now: int, selected: bool) -> Line:
        is_open = not item.get("answer")
        # The panel's `kindColors`: `permission` for a question, its own peach or orange for a request, `inactive`
        # for a notice, and the default for a message.
        style = "subtle" if not is_open or is_snoozed(item, now) else {
            "question": "permission", "request": "request", "notice": "inactive", "message": ""}[item["kind"]]
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
        if item.get("messages"):
            # The messages the person sent about the item, oldest first. The agent answers them in its
            # conversation.
            lines.append(Line([]))
            for said in item["messages"]:
                first, *rest = textwrap.wrap(said.get("text", ""), max(1, wrap - len("you: "))) or [""]
                lines.append(Line([(indent + "you: ", "subtle"), (first, "")]))
                lines += [Line([(indent + " " * len("you: ") + part, "")]) for part in rest]
        lines += self.answer_lines(item, indent, wrap)
        stage = stage_of(item)
        if item.get("start") and not item.get("answer"):
            reason = (item.get("progress") or {}).get("reason")
            said = f"Blocked: {reason}" if stage == "blocked" else f"{(stage or 'start').capitalize()}: {item['start']}"
            lines += [Line([]), Line([(indent + said, STAGE_STYLES.get(stage or "", ""))])]
        controls = self.controls()
        options = [one for one in controls if one.option]
        _, focused = self.focus()
        if options:
            lines.append(Line([]))
        for one in options:
            if one.key != focused:
                lines.append(Line([(f"{indent}{one.key}: {one.label}", "")]))
                continue
            lines.append(Line([(f"{indent[:-2]}› {one.key}: {one.label}", "")], selected=True, spot=True))
            if self.field is not None and self.field.inline and self.field.target == item["ref"]:
                lines.append(self.note_line(self.field, indent + " " * (len(one.key) + 2), width))
        rest = [one for one in controls if not one.option]
        if rest:
            lines += [Line([]), self.controls_line(rest)]
        return lines

    def entry_line(self, entry: dict, now: int, selected: bool) -> Line:
        """A repository inbox entry's line, by its role: a peer's item in its kind's style, a sent item or a reply
        muted once it is closed."""
        style = {"question": "permission", "request": "request", "notice": "inactive"}.get(entry.get("kind", ""), "")
        if entry["role"] != "incoming":
            style = "subtle" if repo_inbox.is_closed(entry) else ""
        parts = [(f"- {entry['line']}", style)]
        marks = [entry["state"]] if entry["state"] in ("replied", "closed") else []
        if entry["role"] == "incoming" and entry.get("reply_count"):
            marks.append(f"{entry['reply_count']} {'reply' if entry['reply_count'] == 1 else 'replies'}")
        parts += [(f"  {mark}", "subtle") for mark in marks]
        parts.append((f"  {age(now - entry.get('opened_at', now))}", "subtle"))
        return Line(parts, selected)

    def entry_drawer(self, entry: dict, entries: list[dict], width: int) -> list[Line]:
        indent = " " * DRAWER_INDENT
        wrap = max(10, width - DRAWER_INDENT)
        said = [entry.get("detail") or "", f"follow-up: {entry['follow_up']}" if entry.get("follow_up") else ""]
        if entry["role"] == "reply":
            said = [entry.get("text") or ""]
        lines: list[Line] = []
        for text in [part for part in said if part]:
            lines.append(Line([]))
            for paragraph in text.splitlines():
                lines += [Line([(indent + part, "")]) for part in textwrap.wrap(paragraph, wrap) or [""]]
        if entry["role"] == "sent":
            replies = [found for found in entries if found["role"] == "reply" and found.get("answers") == entry["id"]]
            if replies:
                lines.append(Line([]))
            lines += [Line([(indent + part, "")]) for found in replies for part in textwrap.wrap(found["line"], wrap)]
        controls = self.controls()
        _, focused = self.focus()
        options = [one for one in controls if one.option]
        if options:
            lines.append(Line([]))
        for one in options:
            if one.key == focused:
                lines.append(Line([(f"{indent[:-2]}› {one.key}: {one.label}", "")], selected=True, spot=True))
            else:
                lines.append(Line([(f"{indent}{one.key}: {one.label}", "")]))
        rest = [one for one in controls if not one.option]
        if rest:
            lines += [Line([]), self.controls_line(rest)]
        return lines

    def note_line(self, typing: Field, indent: str, width: int) -> Line:
        """The note field, under the option it records, showing the stretch of what was typed around the cursor."""
        before = indent + typing.prompt
        space = max(1, width - len(before) - 1)
        start = max(0, typing.cursor - space)
        return Line([(before, "bold"), (typing.text[start:start + space], "")], spot=True,
                    cursor=len(before) + typing.cursor - start)

    def answer_lines(self, item: dict, indent: str, wrap: int) -> list[Line]:
        """What a resolved item's drawer says became of it, and the note the person added."""
        answered = item.get("answer")
        if not answered:
            return []
        if answered.get("delivered"):
            said = "Delivered"
        elif answered.get("withdrawn"):
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
        lines = [Line([]), Line([(f"{indent}{said}", ""), (" ✓", "success")])]
        if answered.get("note"):
            lines += [Line([(indent + part, "subtle")])
                      for part in textwrap.wrap(f"note: {answered['note']}", wrap)]
        return lines

    def controls_line(self, controls: list[Control]) -> Line:
        """The drawer's actions on one line, the focused one after a `›` in the gap before it."""
        _, focused = self.focus()
        parts: list[tuple[str, str]] = []
        lit = None
        for at, one in enumerate(controls):
            gap = " " * DRAWER_INDENT if at == 0 else "   "
            label = f"{one.key}: {one.label}"
            if one.key == focused:
                parts.append((f"{gap[:-2]}› ", ""))
                lit = len(parts)
                parts.append((label, ""))
            else:
                parts.append((gap + label, ""))
        return Line(parts, spot=lit is not None, lit=lit)


# The terminal

CURSES_KEYS = {"KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_ENTER": "enter",
               "KEY_BACKSPACE": "backspace", "KEY_DC": "delete", "KEY_HOME": "home", "KEY_END": "end",
               "KEY_PPAGE": "pageup", "KEY_NPAGE": "pagedown"}
CHARACTER_KEYS = {"\n": "enter", "\r": "enter", "\x1b": "esc", "\x7f": "backspace", "\b": "backspace",
                  "\x01": "home", "\x05": "end", "\x03": "ctrl-c"}
# The cursor and editing keys by the last byte of their escape sequence, or by its number and `~`. A terminal sends
# `ESC [` before them, or `ESC O` before an arrow in application mode; curses names only the form the terminfo
# entry lists, and hands over the rest of the other byte by byte.
ESCAPES = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "1~": "home", "7~": "home",
           "4~": "end", "8~": "end", "3~": "delete", "5~": "pageup", "6~": "pagedown"}
# How long to wait for the rest of an escape sequence, in milliseconds, and how long `get_wch` waits for a key
# before the screen is drawn again.
ESCAPE_WAIT = 50
REDRAW_WAIT = 250

# The colors of Claude Code's theme keys that the panel draws with, in Claude Code 2.1.326's default `dark` theme
# and its `light` theme, as the theme tables in the JavaScript bundled into the `claude` executable define them.
# They were read by a script that searched the executable for `permission:"rgb(` and printed each table around a
# match, and the function there that maps a theme's name to its table. `request` is no theme key: it is
# `hooks/register.tsx`'s own `OTHER_REQUEST_COLOR` and `REQUEST_COLORS` for a light theme.
THEMES: dict[str, dict[str, tuple[int, int, int]]] = {
    "dark": {"permission": (177, 185, 249), "suggestion": (177, 185, 249), "claude": (215, 119, 87),
             "inactive": (153, 153, 153), "subtle": (80, 80, 80), "success": (78, 186, 101),
             "error": (255, 107, 128), "warning": (255, 193, 7), "request": (255, 190, 140)},
    "light": {"permission": (87, 105, 247), "suggestion": (87, 105, 247), "claude": (215, 119, 87),
              "inactive": (102, 102, 102), "subtle": (175, 175, 175), "success": (44, 122, 57),
              "error": (171, 43, 63), "warning": (150, 108, 30), "request": (180, 90, 40)},
}
# The panel's two terminal palette colors, `ansi256(4)` for a fixer and `ansi256(6)` for a branch, which are the
# same slot among 8 colors as among 256.
PALETTE = {"fixer": 4, "branch": 6}
# Each theme key's color on a terminal of 8 colors, after Claude Code's `dark-ansi` theme. `subtle` has none and
# draws dim. A request has no ansi color of its own in the panel, so it shares `warning`'s yellow.
BASIC = {"permission": "COLOR_BLUE", "suggestion": "COLOR_BLUE", "claude": "COLOR_RED", "inactive": "COLOR_WHITE",
         "success": "COLOR_GREEN", "error": "COLOR_RED", "warning": "COLOR_YELLOW", "request": "COLOR_YELLOW"}
# The six levels of each channel in xterm's 6×6×6 color cube, colors 16 to 231.
CUBE = (0, 95, 135, 175, 215, 255)


def key_name(pressed: object, curses: Any) -> Optional[str]:
    """The name `Console.key` takes for what `get_wch` returned."""
    if isinstance(pressed, str):
        return CHARACTER_KEYS.get(pressed, pressed)
    return next((name for constant, name in CURSES_KEYS.items() if pressed == getattr(curses, constant, None)), None)


def xterm_color(rgb: tuple[int, int, int]) -> int:
    """The xterm-256 color nearest `rgb`, from the color cube and the gray ramp. Colors 0 to 15 are left out,
    because each terminal's palette sets them."""
    colors = [(16 + 36 * red + 6 * green + blue, (CUBE[red], CUBE[green], CUBE[blue]))
              for red in range(6) for green in range(6) for blue in range(6)]
    colors += [(232 + step, (8 + 10 * step,) * 3) for step in range(24)]
    return min(colors, key=lambda color: sum((one - other) ** 2 for one, other in zip(color[1], rgb)))[0]


def theme_name(environ: Optional[dict] = None) -> str:
    """`light` when `COLORFGBG`, which some terminals set, names a light background, white or bright white, and
    otherwise `dark`, Claude Code's default."""
    environ = os.environ if environ is None else environ
    background = (environ.get("COLORFGBG") or "").split(";")[-1]
    return "light" if background in ("7", "15") else "dark"


def styles(curses: Any) -> dict[str, int]:
    """Each style's attributes: the panel's colors on the terminal's own background, as the nearest of 256 colors
    or one of 8, or none under `NO_COLOR`."""
    found = {"": 0, "subtle": curses.A_DIM, "bold": curses.A_BOLD}
    names = [*THEMES["dark"], *PALETTE]
    if os.environ.get("NO_COLOR") or not curses.has_colors():
        found.update({name: curses.A_BOLD if name == "error" else 0 for name in names if name != "subtle"})
        return found
    curses.start_color()
    curses.use_default_colors()
    theme = THEMES[theme_name()]
    rich = getattr(curses, "COLORS", 8) >= 256
    for index, name in enumerate(names, 1):
        if name in PALETTE:
            color = PALETTE[name]
        elif rich:
            color = xterm_color(theme[name])
        elif name in BASIC:
            color = getattr(curses, BASIC[name])
        else:
            continue
        curses.init_pair(index, color, -1)
        found[name] = curses.color_pair(index)
    return found


def printable(text: str) -> str:
    """Replace control characters so a subject or detail cannot move the terminal's cursor."""
    return "".join(character if character.isprintable() else " " for character in text)


class Screen:
    """Draws a `Console` with curses and feeds it keys."""

    def __init__(self, window: Any, console: Console, curses: Any, out: Optional[TextIO] = None) -> None:
        self.window, self.console, self.curses = window, console, curses
        # Where escape sequences that curses does not draw, such as the clipboard's, go to the terminal.
        self.out = sys.stdout if out is None else out
        self.styles = styles(curses)
        # What `read_key` read past the end of an escape sequence, to hand over next.
        self.pending: list[object] = []

    def put(self, y: int, line: Line, width: int) -> None:
        x = 1
        for at, (text, style) in enumerate(line.parts):
            if x >= width:
                break
            lit = line.selected or at == line.lit
            attributes = self.styles.get(style, 0) | (self.curses.A_REVERSE if lit else 0)
            shown = printable(text)[:width - x]
            try:
                self.window.addstr(y, x, shown, attributes)
            except self.curses.error:
                pass
            x += len(shown)

    def draw(self) -> None:
        height, width = self.window.getmaxyx()
        footer = self.console.footer()
        typing = self.console.field
        below = typing is not None and not typing.inline
        room = max(1, height - len(footer) - (1 if below else 0))
        shown = self.console.viewport(max(0, width - 1), room)
        self.window.erase()
        for y, line in enumerate(shown):
            self.put(y, line, width)
        for y, line in enumerate(footer, room):
            self.put(y, line, width)
        at = None
        if typing is not None and below:
            space = max(1, width - len(typing.prompt) - 2)
            start = max(0, typing.cursor - space)
            self.put(height - 1, Line([(typing.prompt, "bold"), (typing.text[start:start + space], "")]), width)
            at = (height - 1, min(width - 1, 1 + len(typing.prompt) + typing.cursor - start))
        elif typing is not None:
            at = next(((y, min(width - 1, 1 + line.cursor)) for y, line in enumerate(shown) if line.cursor is not None),
                      None)
        if at is not None:
            self.window.move(*at)
        try:
            self.curses.curs_set(0 if at is None else 1)
        except self.curses.error:
            pass
        self.window.refresh()
        # After curses has written the screen, so the sequence reaches the terminal whole and is never drawn as text.
        copied = self.console.take_clipboard()
        if copied is not None:
            self.out.write(clipboard_sequence(copied))
            self.out.flush()

    def read_key(self) -> Optional[str]:
        """The next key's name, or None for one the console does not use. An escape sequence curses did not name
        is read to its last byte and named by `ESCAPES`. Raises `curses.error` when no key came in time."""
        pressed = self.pending.pop(0) if self.pending else self.window.get_wch()
        if pressed != "\x1b":
            return key_name(pressed, self.curses)
        follow = self.follow()
        if follow not in ("[", "O"):
            if follow is not None:
                self.pending.append(follow)
            return "esc"
        body = ""
        while len(body) < 8:
            byte = self.follow()
            if not isinstance(byte, str):
                break
            body += byte
            if "@" <= byte <= "~":
                break
        final = body[-1:]
        return ESCAPES.get(body[:-1].split(";")[0] + final if final == "~" else final)

    def follow(self) -> object:
        """The key after an Esc, when one comes within `ESCAPE_WAIT` milliseconds, or None."""
        self.window.timeout(ESCAPE_WAIT)
        try:
            return self.window.get_wch()
        except self.curses.error:
            return None
        finally:
            self.window.timeout(REDRAW_WAIT)

    def run(self) -> int:
        """Draw and take keys until the console quits. The terminal's Ctrl-C reaches curses as SIGINT, which only
        sets a flag here, so it is a key like any other and never stops the console between two of its steps."""
        self.window.keypad(True)
        self.window.timeout(REDRAW_WAIT)
        interrupted = threading.Event()
        previous = signal.signal(signal.SIGINT, lambda number, frame: interrupted.set())
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
                if interrupted.is_set():
                    interrupted.clear()
                    self.console.key("ctrl-c")
                    continue
                self.draw()
                try:
                    name = self.read_key()
                except self.curses.error:
                    continue
                if name is not None:
                    self.console.key(name)
        finally:
            signal.signal(signal.SIGINT, previous)
            stop.set()
            self.console.refresh_now.set()
        return 0


def main(wake: Callable[[str, dict], None], ensure: Callable[[], object] = lambda: None) -> int:
    """Run the console in this terminal until Ctrl-C quits it."""
    import curses

    os.environ.setdefault("ESCDELAY", "25")
    console = Console(watch.state_root(), wake=wake, ensure=ensure)
    try:
        return curses.wrapper(lambda window: Screen(window, console, curses).run())
    except KeyboardInterrupt:
        return 0
