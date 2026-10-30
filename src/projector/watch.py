"""One daemon per machine that watches the pull requests agent sessions fix and review.

A session subscribes by writing lines to its folder under the state root, and every `project watch`
command but `serve` starts the daemon when no process holds its lock. Each pass, the daemon asks
GitHub a conditional question about each pull request a live session subscribes to, runs the GraphQL
query only for one that changed or is due a full query, picks one owner for each pull request and
role, and appends what the owner should do to the owner's `events`. It exits once no session has
been live for five minutes.

Layout of the state root, `$XDG_STATE_HOME/projector/watch/`:

    daemon.mutex                                the lock the running daemon holds
    daemon.json                                 its pid, version, start time, and operator
    daemon.log                                  its output
    stop                                        a pid: the daemon with that pid exits
    status.json                                 live sessions, targets, the last pass, and GraphQL spent
    owners.json                                 the session that owns each pull request and role
    sessions/<session>/subscriptions            one subscription a line
    sessions/<session>/session.json             the deliverer, and when it last renewed the session
    sessions/<session>/events                   event lines not yet delivered
    sessions/<session>/state.json               what the daemon announced to the session, and when
    cache/<owner>/<repo>/pulls/<number>.json    a pull request's ETags and last result
    cache/<owner>/<repo>/branches/<base>.json   a base branch's ETag
    cache/<owner>/<repo>/open/<page>.json       a page of the repository's open pull requests
    cache/<owner>/<repo>/adopted.json           the pull requests a repository subscription covers

Owner and repository names under `cache/` are lowercased, because GitHub ignores their case.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from . import review
from .config import MINIMUMS, ConfigError
from .config import load as load_config
from .core import ProjectorError, UsageError
from .inbox import PULL, SESSION, locked, read_json, try_lock, unlock, write_json

LAPSE = 300.0
IDLE = 300.0
SLEPT = 60.0
FORGET = 7 * 24 * 3600.0
STOP_WAIT = 10.0
RENEW_EVERY = 30.0
PAGE = 100
SNIPPET = 130
DELIVERERS = ("mod", "stream")
GONE = "gone"
WATCH_SETTINGS = ("interval", "full_every", "renotify", "review_stall")

REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
REPO_ONLY = re.compile(rf"^{REPO}$")
LINE = re.compile(
    rf"^(?P<excluded>!)?(?P<repo>{REPO})(?:#(?P<number>[1-9][0-9]*))?\s+"
    r"(?:(?P<fix>fix)(?:\s+(?P<auto>auto))?|review\s+(?P<model>\S+)\s+(?P<reviewer>[A-Za-z0-9-]+))$"
)
PULL_URL = re.compile(rf"^https?://github\.com/(?P<repo>{REPO})/pull/(?P<number>[1-9][0-9]*)(?:[/?#].*)?$")
PULL_URL_IN_TEXT = re.compile(rf"https?://github\.com/({REPO})/pull/([1-9][0-9]*)")
PUSH_REMOTE = re.compile(r"^To (\S+)$")
PUSH_UPDATE = re.compile(r"^\s*\+?\s*[0-9a-f]{4,}\.\.\.?[0-9a-f]{4,}\s+\S+\s+->\s+(\S+)")
GITHUB_REMOTE = re.compile(rf"github\.com[:/]({REPO}?)(?:\.git)?/?$")
STATUS_LINE = re.compile(r"^HTTP/\S+\s+([0-9]{3})\b")
HEADER_END = re.compile(r"\r?\n\r?\n")
HTML_COMMENT = re.compile(r"<!--(?:[^-]|-[^-]|--[^>])*-->")
HTML_TAG = re.compile(r"<[^>]*>")
ANSWERED = re.compile(r"projector-(?:finding|verify)")

# The query § 2.6 of the watch-daemon plan gives, which Projector's panel reads from the cache too, with
# the fields the events name: `mergedAt` and `mergeCommit` for MERGED, each comment's id and times for a
# live start comment, and `nameWithOwner` for the review state's path. `rateLimit` reports what it cost.
QUERY = """query($o:String!,$r:String!,$n:Int!){ rateLimit { cost }
  repository(owner:$o,name:$r){ visibility nameWithOwner
    pullRequest(number:$n){
      number title url body state isDraft reviewDecision mergeable updatedAt mergedAt mergeCommit { oid }
      headRefOid headRefName baseRefName author { login }
      additions deletions changedFiles
      commits(last:1){ nodes{ commit{ statusCheckRollup{ state } } } }
      comments(last:100){ nodes{ databaseId author{ login } body createdAt updatedAt } }
      reviews(last:100){ nodes{ author{ login } authorAssociation submittedAt body } }
      changesRequested: reviews(states:[CHANGES_REQUESTED], last:1){ nodes{ author{ login } body commit{ oid } } }
      reviewThreads(first:100){ nodes{ id isResolved path line
        first: comments(first:1){ nodes{ databaseId body } }
        last: comments(last:1){ nodes{ databaseId author{ login } body } } } } } } }"""


class Excluded(ProjectorError):
    """The session drops the pull request from the role it asked to add."""


# Paths and time

def state_root(environ: Optional[dict] = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "projector" / "watch"


def session_folder(root: Path, session: str) -> Path:
    if not SESSION.match(session):
        raise UsageError(f"{session!r} is not a session id: use letters, digits, `.`, `_`, and `-`")
    return root / "sessions" / session


def session_names(root: Path) -> list[str]:
    folder = root / "sessions"
    if not folder.is_dir():
        return []
    return sorted(path.name for path in folder.iterdir() if path.is_dir() and SESSION.match(path.name))


def cache_dir(root: Path, repo: str) -> Path:
    return root / "cache" / repo.lower()


def pull_cache(root: Path, repo: str, number: int) -> Path:
    return cache_dir(root, repo) / "pulls" / f"{number}.json"


def stamp(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")


def seconds_of(text: object) -> Optional[float]:
    if not isinstance(text, str):
        return None
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def write_text(path: Path, text: str) -> None:
    """Replace the file whole, so the daemon, which reads without the lock, never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def file_signature(path: Path) -> Optional[tuple[int, int, int]]:
    try:
        found = path.stat()
    except OSError:
        return None
    return found.st_mtime_ns, found.st_size, found.st_ino


def session_id(explicit: Optional[str], required: bool = True, environ: Optional[dict] = None) -> Optional[str]:
    """The session a command acts for: `--session`, then `PROJECTOR_SESSION`, then the id Claude Code gives
    every command it runs, a subagent's included."""
    environ = os.environ if environ is None else environ
    session = explicit or environ.get("PROJECTOR_SESSION") or environ.get("CLAUDE_CODE_SESSION_ID") or None
    if session is None:
        if required:
            raise UsageError("this command needs the session: pass --session, or set PROJECTOR_SESSION")
        return None
    if not SESSION.match(session):
        raise UsageError(f"{session!r} is not a session id: use letters, digits, `.`, `_`, and `-`")
    return session


# Subscriptions

@dataclass(frozen=True)
class Subscription:
    """One line of a session's `subscriptions`: a pull request or, for review, a repository, and a role."""

    repo: str
    number: Optional[int]
    role: str
    model: Optional[str] = None
    reviewer: Optional[str] = None
    auto: bool = False
    excluded: bool = False

    @classmethod
    def parse(cls, text: str) -> Optional["Subscription"]:
        match = LINE.match(text.strip())
        if match is None:
            return None
        number = int(match["number"]) if match["number"] else None
        excluded = bool(match["excluded"])
        if match["fix"]:
            if number is None or (excluded and match["auto"]):
                return None
            return cls(match["repo"], number, "fix", auto=bool(match["auto"]), excluded=excluded)
        if excluded and number is None:
            return None
        return cls(match["repo"], number, "review", match["model"], match["reviewer"], excluded=excluded)

    @property
    def target(self) -> str:
        return self.repo if self.number is None else f"{self.repo}#{self.number}"

    @property
    def text(self) -> str:
        role = ("fix auto" if self.auto else "fix") if self.role == "fix" else f"review {self.model} {self.reviewer}"
        return f"{'!' if self.excluded else ''}{self.target} {role}"

    @property
    def key(self) -> str:
        return self.text.lower()

    @property
    def role_name(self) -> str:
        """The role an owner is chosen for: `fix`, or `review` with the model's identity."""
        return "fix" if self.role == "fix" else f"review {review.identity(self.model or '')}"

    def same_role(self, other: "Subscription") -> bool:
        return (self.repo.lower() == other.repo.lower() and self.number == other.number
                and self.role_name == other.role_name)

    def rank(self, added: float) -> tuple:
        """Precedence among sessions: a pull request's line beats its repository's, and the newest line wins,
        except that an `auto` line outranks no line older than itself."""
        if self.role == "fix":
            return (0, -added) if self.auto else (1, added)
        return (0 if self.number is None else 1, added)


def matches(line: Subscription, repo: str, number: Optional[int], role: Optional[str], model: Optional[str]) -> bool:
    return (line.repo.lower() == repo.lower() and line.number == number and role in (None, line.role)
            and (model is None or line.role == "fix" or review.identity(line.model or "") == review.identity(model)))


def read_lines(folder: Path) -> list[str]:
    try:
        return (folder / "subscriptions").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def subscriptions(folder: Path) -> list[Subscription]:
    return [line for line in map(Subscription.parse, read_lines(folder)) if line is not None]


def edit_subscriptions(folder: Path, edit: Callable[[list[str]], list[str]]) -> None:
    with locked(folder):
        before = read_lines(folder)
        after = edit(list(before))
        if after != before:
            write_text(folder / "subscriptions", "".join(f"{line}\n" for line in after))


def parse_target(text: str) -> tuple[str, Optional[int]]:
    """`owner/repo#number`, a pull request's URL, or `owner/repo`."""
    text = text.strip()
    for pattern in (PULL, PULL_URL):
        match = pattern.match(text)
        if match:
            return match["repo"], int(match["number"])
    if REPO_ONLY.match(text):
        return text, None
    raise UsageError(f"{text!r} is not owner/repo#number, a pull request URL, or owner/repo")


def add(root: Path, session: str, wanted: Subscription) -> Optional[str]:
    """Add the subscription unless the session has it, and answer the line written, if any.

    An explicit `fix` line replaces the session's `fix auto` line for the pull request, and a review line
    naming another reviewer replaces the session's line for that model. A pull request the session drops
    from the role is refused."""
    written: list[str] = []

    def edit(lines: list[str]) -> list[str]:
        parsed = [Subscription.parse(line) for line in lines]
        if any(line and line.excluded and line.same_role(wanted) for line in parsed):
            raise Excluded(f"this session drops {wanted.target} from {wanted.role}; run `project watch restore "
                           f"{wanted.target} --role {wanted.role}` to let it back in")
        for index, line in enumerate(parsed):
            if line is None or line.excluded or not line.same_role(wanted):
                continue
            if (line.auto and not wanted.auto) or (line.role == "review"
                                                    and (line.reviewer or "").lower() != (wanted.reviewer or "").lower()):
                lines[index] = wanted.text
                written.append(wanted.text)
            return lines
        written.append(wanted.text)
        return [*lines, wanted.text]

    edit_subscriptions(session_folder(root, session), edit)
    return written[0] if written else None


def remove(root: Path, session: str, repo: str, number: Optional[int], role: Optional[str],
           model: Optional[str] = None, excluded: bool = False) -> list[str]:
    """Remove the session's plain lines for the target and role, or with `excluded` its `!` lines, and
    answer the lines removed. No role is every role, and no model every model."""
    removed: list[str] = []

    def edit(lines: list[str]) -> list[str]:
        kept = []
        for text in lines:
            line = Subscription.parse(text)
            if line is not None and line.excluded == excluded and matches(line, repo, number, role, model):
                removed.append(line.text)
            else:
                kept.append(text)
        return kept

    folder = session_folder(root, session)
    if (folder / "subscriptions").exists():
        edit_subscriptions(folder, edit)
    return removed


def reviewed_models(lines: list[Subscription], repo: str, number: int) -> dict[str, Subscription]:
    """The session's review lines that cover the pull request, its own or its repository's, by model."""
    covering = [line for line in lines if not line.excluded and line.role == "review"
                and line.repo.lower() == repo.lower() and line.number in (None, number)]
    return {review.identity(line.model or ""): line for line in sorted(covering, key=lambda line: line.number is None)}


def drop(root: Path, session: str, repo: str, number: int, role: str, model: Optional[str] = None,
         reviewer: Optional[str] = None) -> list[str]:
    """Append the `!` lines that keep the pull request out of the role for this session, and answer them.
    For `review` with no model, every model the session reviews the pull request with."""
    appended: list[str] = []

    def edit(lines: list[str]) -> list[str]:
        parsed = [line for line in map(Subscription.parse, lines) if line is not None]
        if role == "fix":
            wanted = [Subscription(repo, number, "fix", excluded=True)]
        else:
            reviewing = reviewed_models(parsed, repo, number)
            if model is not None:
                line = reviewing.get(review.identity(model))
                if line is None and reviewer is None:
                    raise UsageError(f"this session does not review {repo}#{number} with {model}; pass the reviewer")
                pairs = [(model, line.reviewer if line else reviewer)]
            else:
                if not reviewing:
                    raise ProjectorError(f"this session does not review {repo}#{number}; pass --model to drop "
                                         "one model's review anyway")
                pairs = [(line.model, line.reviewer) for line in reviewing.values()]
            wanted = [Subscription(repo, number, "review", name, login, excluded=True) for name, login in pairs]
        for line in wanted:
            if not any(other.excluded and other.same_role(line) for other in parsed):
                appended.append(line.text)
        return [*lines, *appended]

    edit_subscriptions(session_folder(root, session), edit)
    return appended


# Delivery

def renew(root: Path, session: str, deliverer: str, now: float) -> None:
    folder = session_folder(root, session)
    with locked(folder):
        write_json(folder / "session.json", {"deliverer": deliverer, "renewed_at": stamp(now)})


def take(root: Path, session: str, now: float) -> list[str]:
    """Empty the session's events and renew it, keeping its deliverer, and answer the lines taken."""
    folder = session_folder(root, session)
    with locked(folder):
        events = folder / "events"
        try:
            text = events.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        if text:
            events.write_text("", encoding="utf-8")
        deliverer = (read_json(folder / "session.json") or {}).get("deliverer")
        write_json(folder / "session.json", {"deliverer": deliverer if deliverer in DELIVERERS else "mod",
                                             "renewed_at": stamp(now)})
    return [line for line in text.splitlines() if line.strip()]


def delivery(root: Path, session: str, now: float) -> str:
    """How the session's events arrive: `mod` or `stream` while that deliverer renews it, or `none`."""
    record = read_json(session_folder(root, session) / "session.json") or {}
    renewed = seconds_of(record.get("renewed_at"))
    if record.get("deliverer") in DELIVERERS and renewed is not None and now - renewed < LAPSE:
        return record["deliverer"]
    return "none"


def append_events(folder: Path, lines: list[str]) -> None:
    with locked(folder):
        with (folder / "events").open("a", encoding="utf-8") as stream:
            stream.writelines(f"{line}\n" for line in lines)


def stream(root: Path, session: str, emit: Callable[[str], None], ensure: Callable[[], object], *,
           wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
           sleep: Callable[[float], None] = time.sleep) -> None:
    """Renew the session every 30 seconds, starting the daemon if it died, and emit each event as it arrives."""
    events = session_folder(root, session) / "events"
    renewed: Optional[float] = None
    while True:
        if renewed is None or mono() - renewed >= RENEW_EVERY:
            ensure()
            renew(root, session, "stream", wall())
            renewed = mono()
        if events.exists() and events.stat().st_size:
            for line in take(root, session, wall()):
                emit(line)
        sleep(1)


# Subscriptions from a command's output

def pushed(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, str]]]:
    """The pull requests a command's output links, as `gh pr create` prints one, and each branch a
    `git push` to GitHub updated, as `(owner/repo, branch)`."""
    pulls = [(repo, int(number)) for repo, number in PULL_URL_IN_TEXT.findall(text)]
    branches = []
    remote: Optional[str] = None
    for line in text.splitlines():
        line = line.rstrip()
        to = PUSH_REMOTE.match(line)
        if to:
            found = GITHUB_REMOTE.search(to.group(1))
            remote = found.group(1) if found else None
            continue
        update = PUSH_UPDATE.match(line)
        if update and remote:
            branch = update.group(1).removeprefix("refs/heads/")
            branches.append((remote, branch))
    return pulls, branches


def unique(pairs: list[tuple[str, object]]) -> list[tuple[str, object]]:
    seen: set[tuple[str, object]] = set()
    kept = []
    for repo, value in pairs:
        if (repo.lower(), value) not in seen:
            seen.add((repo.lower(), value))
            kept.append((repo, value))
    return kept


def add_from_output(root: Path, session: str, text: str) -> tuple[list[str], list[str]]:
    """Subscribe the session to `fix auto` on each pull request the output opened or pushed to, and answer
    the lines added and a problem for each branch whose pull request could not be looked up."""
    pulls, branches = pushed(text)
    found: list[tuple[str, object]] = list(pulls)
    problems = []
    for repo, branch in unique(branches):
        owner = repo.split("/")[0]
        result = run_gh(["api", f"repos/{repo}/pulls?head={quote(f'{owner}:{branch}', safe=':')}&state=open"])
        try:
            listed = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError:
            listed = None
        if not isinstance(listed, list):
            problems.append(f"could not look up the pull request for {repo} {branch}: "
                            f"{(result.stderr or result.stdout).strip() or 'gh failed'}")
            continue
        found += [(repo, pull["number"]) for pull in listed if isinstance(pull, dict)
                  and isinstance(pull.get("number"), int)]
    added = []
    for repo, number in unique(found):
        try:
            line = add(root, session, Subscription(repo, number, "fix", auto=True))
        except Excluded:
            continue
        if line:
            added.append(line)
    return added, problems


# GitHub

@dataclass(frozen=True)
class GhResult:
    returncode: int
    stdout: str
    stderr: str = ""


def run_gh(args: list[str]) -> GhResult:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return GhResult(127, "", "the gh CLI is not installed")
    return GhResult(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class Response:
    """What `gh api -i` printed: the status line's code, or None when it printed none, the ETag, and the body.
    `gh api` exits 1 on a 304 as on a 404 or a network failure, so only the status line tells them apart."""

    status: Optional[int]
    etag: Optional[str]
    body: str

    def json(self) -> object:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


def parse_response(text: str) -> Response:
    parts = HEADER_END.split(text, maxsplit=1)
    lines = parts[0].splitlines()
    status = STATUS_LINE.match(lines[0]) if lines else None
    etag = next((line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("etag:")), None)
    return Response(int(status.group(1)) if status else None, etag, parts[1] if len(parts) > 1 else "")


def decode_pages(text: str) -> Optional[list]:
    """The items of every page `gh api --paginate` printed, one JSON array after another."""
    decoder = json.JSONDecoder()
    items: list = []
    index, text = 0, text.strip()
    while index < len(text):
        try:
            page, index = decoder.raw_decode(text, index)
        except ValueError:
            return None
        if not isinstance(page, list):
            return None
        items += page
        while index < len(text) and text[index].isspace():
            index += 1
    return items


def trim_review(found: dict) -> dict:
    return {"id": found.get("id"), "state": found.get("state"), "user": {"login": (found.get("user") or {}).get("login")},
            "body": found.get("body") or "", "commit_id": found.get("commit_id"),
            "submitted_at": found.get("submitted_at"), "author_association": found.get("author_association")}


def open_pull(found: dict) -> dict:
    return {"number": found.get("number"), "author": (found.get("user") or {}).get("login"),
            "requested": [(person or {}).get("login") for person in found.get("requested_reviewers") or []]}


def nodes(value: Optional[dict], key: str) -> list[dict]:
    return [node for node in ((value or {}).get(key) or {}).get("nodes") or [] if isinstance(node, dict)]


def login(node: Optional[dict]) -> str:
    return ((node or {}).get("author") or {}).get("login") or ""


def pull_request(entry: object) -> dict:
    repository = entry.get("repository") if isinstance(entry, dict) else None
    found = repository.get("pullRequest") if isinstance(repository, dict) else None
    return found if isinstance(found, dict) else {}


# Settings

@dataclass(frozen=True)
class Settings:
    interval: int = 60
    full_every: int = 600
    renotify: int = 900
    review_stall: int = 1800


def user_settings() -> Settings:
    """The `watch.*` settings in the user's own configuration, since one daemon serves every repository."""
    home = Path(os.environ.get("HOME") or Path.home())
    config = load_config(home, home)
    values = {name: config.setting(f"watch.{name}") for name in WATCH_SETTINGS}
    return Settings(**{name: max(MINIMUMS[f"watch.{name}"], value) for name, value in values.items()
                       if value is not None})


# The daemon

@dataclass
class Seen:
    signature: tuple
    at: float


@dataclass
class Claim:
    session: str
    line: Subscription
    rank: tuple


def pull_label(pull: tuple[str, int]) -> str:
    return f"{pull[0]}#{pull[1]}"


def role_label(pull: tuple[str, int], name: str) -> str:
    return f"{pull_label(pull)} {name}"


def label_pull(label: str) -> tuple[tuple[str, int], str]:
    ref, name = label.split(" ", 1)
    repo, number = ref.rsplit("#", 1)
    return (repo, int(number)), name


class Daemon:
    """The daemon's memory between passes and its clocks: `wall` for times it records, and `mono`, its own
    clock, for liveness, which it forgives a gap in, as when the machine sleeps."""

    def __init__(self, root: Path, *, version: str = "unknown", gh: Optional[Callable[[list[str]], GhResult]] = None,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 settings: Optional[Settings] = None, review_root: Optional[Path] = None,
                 pid: Optional[int] = None) -> None:
        self.root = root
        self.version = version
        self.gh = gh or (lambda args: run_gh(args))
        self.wall = wall
        self.mono = mono
        self.fixed_settings = settings
        self.settings = settings or Settings()
        self.review_root = review_root
        self.pid = os.getpid() if pid is None else pid
        self.started_at = wall()
        self.operator: Optional[str] = None
        self.operator_read: Optional[float] = None
        self.sessions: dict[str, Seen] = {}
        self.marks: dict[str, Optional[tuple]] = {}
        self.live: set[str] = set()
        self.locks: dict[Path, tuple[float, float]] = {}
        self.spent: list[tuple[float, int]] = []
        self.targets: list[str] = []
        self.repositories: list[str] = []
        self.last_tick: Optional[float] = None
        self.last_live = mono()
        self.next_pass = mono()
        self.wanted = False
        self.last_pass: Optional[float] = None

    def log(self, message: str) -> None:
        print(f"{stamp(self.wall())} {message}", flush=True)

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_daemon()
        self.read_operator(self.wall())

    def write_daemon(self) -> None:
        write_json(self.root / "daemon.json", {"pid": self.pid, "version": self.version,
                                               "started_at": stamp(self.started_at), "operator": self.operator})

    def read_operator(self, now: float) -> None:
        """Read the login `gh` is signed in as at the start and on each full pass, and drop every ETag when it
        changes, because GitHub varies an ETag with the token."""
        if self.operator is not None and self.operator_read is not None \
                and now - self.operator_read < self.settings.full_every:
            return
        result = self.gh(["api", "user"])
        try:
            found = json.loads(result.stdout).get("login") if result.returncode == 0 else None
        except (ValueError, AttributeError):
            found = None
        if not isinstance(found, str) or not found:
            return
        self.operator_read = now
        if found != self.operator:
            if self.operator is not None:
                self.drop_etags()
            self.operator = found
            self.write_daemon()

    def drop_etags(self) -> None:
        cache = self.root / "cache"
        for path in cache.glob("*/*/pulls/*.json"):
            entry = read_json(path)
            if entry is not None:
                write_json(path, {**entry, "etags": {}})
        for pattern in ("*/*/branches/*.json", "*/*/open/*.json"):
            for path in cache.glob(pattern):
                path.unlink(missing_ok=True)

    def step(self) -> bool:
        try:
            return self.tick()
        except Exception:
            self.log(traceback.format_exc().rstrip())
            self.next_pass = self.mono() + self.settings.interval
            return True

    def tick(self) -> bool:
        """One turn of the loop, once a second: answer whether the daemon keeps running. The gap that reads as
        the machine sleeping runs from the end of the last turn, so a pass that outlasts `SLEPT` is no sleep."""
        now = self.mono()
        if self.last_tick is not None and now - self.last_tick > SLEPT:
            self.forgive(now)
        try:
            if self.stop_requested():
                self.log("stopping: the stop file names this daemon")
                return False
            self.scan(now)
            if self.live:
                self.last_live = now
            elif now - self.last_live >= IDLE:
                self.log("stopping: no session has been live for 5 minutes")
                return False
            if self.wanted or now >= self.next_pass:
                self.run_pass()
            return True
        finally:
            self.last_tick = self.mono()

    def forgive(self, now: float) -> None:
        """Count every live session as seen, and every lock as changed, now: a gap in the loop is the machine
        sleeping, which should lapse no session and stall no review."""
        for name in self.live:
            self.sessions[name] = Seen(self.sessions[name].signature, now)
        self.locks = {path: (modified, now) for path, (modified, _) in self.locks.items()}

    def stop_requested(self) -> bool:
        stop = self.root / "stop"
        try:
            named = stop.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if named != str(self.pid):
            return False
        stop.unlink(missing_ok=True)
        return True

    def scan(self, now: float) -> None:
        """Note, on the daemon's clock, each session whose `session.json` changed, and ask for a pass at once
        when a session becomes live or subscribes to something the cache has no result for."""
        wall = self.wall()
        names = session_names(self.root)
        for name in set(self.sessions) - set(names):
            del self.sessions[name]
        for name in names:
            folder = self.root / "sessions" / name
            signature = file_signature(folder / "session.json")
            seen = self.sessions.get(name)
            if signature is None:
                self.sessions.pop(name, None)
            elif seen is None:
                self.sessions[name] = Seen(signature, now - max(0.0, wall - renewed_at(folder)))
            elif seen.signature != signature:
                self.sessions[name] = Seen(signature, now)
        live = {name for name, seen in self.sessions.items() if now - seen.at < LAPSE}
        for name in names:
            mark = file_signature(self.root / "sessions" / name / "subscriptions")
            if name in live and mark != self.marks.get(name) and self.lacks_result(name):
                self.wanted = True
            self.marks[name] = mark
        if live - self.live:
            self.wanted = True
        self.live = live

    def lacks_result(self, name: str) -> bool:
        for line in subscriptions(self.root / "sessions" / name):
            if line.excluded:
                continue
            if line.number is not None and not pull_cache(self.root, line.repo, line.number).exists():
                return True
            if line.number is None and not (cache_dir(self.root, line.repo) / "open" / "1.json").exists():
                return True
        return False

    def run_pass(self) -> None:
        self.wanted = False
        self.root.mkdir(parents=True, exist_ok=True)
        if self.fixed_settings is None:
            try:
                self.settings = user_settings()
            except ConfigError as error:
                self.log(f"keeping the settings in use: {error}")
        now_mono = self.mono()
        self.next_pass = now_mono + self.settings.interval
        self.scan(now_mono)
        now = self.wall()
        self.read_operator(now)
        Pass(self, now, now_mono).run()
        self.last_pass = now
        self.forget_sessions(now)
        self.write_status(now)

    def forget_sessions(self, now: float) -> None:
        """Delete the folder of a session lapsed for 7 days."""
        for name in session_names(self.root):
            if name in self.live:
                continue
            folder = self.root / "sessions" / name
            with contextlib.suppress(OSError):
                newest = max((path.stat().st_mtime for path in folder.iterdir()), default=folder.stat().st_mtime)
                if now - newest > FORGET:
                    shutil.rmtree(folder, ignore_errors=True)

    def write_status(self, now: float) -> None:
        self.spent = [(at, cost) for at, cost in self.spent if at > now - 3600]
        write_json(self.root / "status.json", {
            "pid": self.pid,
            "last_pass": stamp(self.last_pass) if self.last_pass is not None else None,
            "live_sessions": sorted(self.live),
            "targets": self.targets,
            "repositories": self.repositories,
            "graphql": [{"at": stamp(at), "cost": cost} for at, cost in self.spent],
        })

    def conditional(self, path: str, etag: Optional[str]) -> Response:
        args = ["api", "-i", *(["-H", f"If-None-Match: {etag}"] if etag else []), path]
        return parse_response(self.gh(args).stdout)

    def query(self, repo: str, number: int, now: float) -> Optional[dict]:
        owner, name = repo.split("/")
        result = self.gh(["api", "graphql", "-f", f"query={QUERY}", "-f", f"o={owner}", "-f", f"r={name}",
                          "-F", f"n={number}"])
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") or {}
        if data:
            self.spent.append((now, ((data.get("rateLimit") or {}).get("cost")) or 1))
        repository = data.get("repository")
        if result.returncode != 0 or payload.get("errors") or not isinstance(repository, dict) \
                or not isinstance(repository.get("pullRequest"), dict):
            return None
        return repository

    def read_reviews(self, repo: str, number: int) -> Optional[list[dict]]:
        result = self.gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews?per_page={PAGE}"])
        found = decode_pages(result.stdout) if result.returncode == 0 else None
        return None if found is None else [trim_review(item) for item in found if isinstance(item, dict)]

    def observe(self, lock: "review.ReviewLock", now: float) -> None:
        seen = self.locks.get(lock.path)
        if seen is None or seen[0] != lock.modified:
            self.locks[lock.path] = (lock.modified, now)

    def stalled(self, lock: "review.ReviewLock", now: float) -> bool:
        seen = self.locks.get(lock.path)
        return seen is not None and now - seen[1] >= self.settings.review_stall


def renewed_at(folder: Path) -> float:
    record = read_json(folder / "session.json") or {}
    renewed = seconds_of(record.get("renewed_at"))
    if renewed is not None:
        return renewed
    try:
        return (folder / "session.json").stat().st_mtime
    except OSError:
        return 0.0


class SessionState:
    """The daemon's record for one session: when it first saw each line, what it announced, and when."""

    KEYS = ("added", "fix", "review", "closed", "adopted")

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.loaded = read_json(folder / "state.json") or {}
        self.data = json.loads(json.dumps(self.loaded))
        for key in self.KEYS:
            if not isinstance(self.data.get(key), dict):
                self.data[key] = {}

    def save(self) -> None:
        if self.data != self.loaded:
            write_json(self.folder / "state.json", self.data)


class Pass:
    """One pass over every live session's subscriptions: gather, adopt, ask, query, choose owners, announce."""

    def __init__(self, daemon: Daemon, now: float, now_mono: float) -> None:
        self.daemon = daemon
        self.root = daemon.root
        self.settings = daemon.settings
        self.now = now
        self.mono = now_mono
        self.states = {name: SessionState(daemon.root / "sessions" / name) for name in sorted(daemon.live)}
        self.outbox: dict[str, list[str]] = {}
        self.branches: dict[tuple[str, str], Optional[str]] = {}
        self.lock_lists: dict[tuple[str, int], list] = {}
        self.stalled_roles: set[tuple[tuple[str, int], str]] = set()

    def tell(self, session: str, *lines: str) -> None:
        self.outbox.setdefault(session, []).extend(lines)

    def run(self) -> None:
        lines = {name: self.note(name) for name in self.states}
        repositories = {line.repo.lower(): line.repo for found in lines.values() for line in found
                        if line.role == "review" and line.number is None and not line.excluded}
        adopted: dict[str, set[int]] = {}
        for repo, spelling in sorted(repositories.items()):
            numbers = self.adopt(spelling)
            if numbers is None:
                self.lose_repository(spelling, lines)
            else:
                adopted[repo] = numbers
        roles, spellings = self.claims(lines, adopted)
        fixed = {pull for pull, name in roles if name == "fix"}
        results = {}
        for pull, spelling in sorted(spellings.items()):
            result = self.refresh(pull, spelling, pull in fixed)
            if result is GONE:
                self.lose_pull(pull, spelling, adopted)
            else:
                results[pull] = result
        roles = {role: claims for role, claims in roles.items() if role[0] in results}
        owners = self.choose(roles, spellings)
        closing = self.route(roles, owners, results, spellings)
        for pull in closing:
            self.close(pull, roles, owners, results[pull], spellings[pull], adopted)
        self.daemon.targets = sorted(f"{spellings[pull]}#{pull[1]}" for pull in results)
        self.daemon.repositories = sorted(spelling for repo, spelling in repositories.items() if repo in adopted)
        self.save(owners)

    def save(self, owners: dict[str, str]) -> None:
        previous = read_json(self.root / "owners.json")
        if owners != previous:
            write_json(self.root / "owners.json", owners)
        for name, state in self.states.items():
            owned = {label for label, owner in owners.items() if owner == name}
            for role in ("fix", "review"):
                state.data[role] = {label: record for label, record in state.data[role].items() if label in owned}
            state.data["closed"] = {label: at for label, at in state.data["closed"].items()
                                    if isinstance(at, (int, float)) and self.now - at < FORGET}
            state.save()
        for session, lines in self.outbox.items():
            if lines:
                append_events(self.root / "sessions" / session, lines)
        seen = {lock.path for found in self.lock_lists.values() for lock in found}
        self.daemon.locks = {path: value for path, value in self.daemon.locks.items() if path in seen}

    # Gather and adopt

    def note(self, name: str) -> list[Subscription]:
        """The session's lines, recording when the daemon first saw each, as the time the file last changed."""
        folder = self.root / "sessions" / name
        found = subscriptions(folder)
        try:
            changed = (folder / "subscriptions").stat().st_mtime
        except OSError:
            changed = self.now
        added = self.states[name].data["added"]
        current = {line.key for line in found if not line.excluded}
        for key in current - added.keys():
            added[key] = changed
        for key in set(added) - current:
            del added[key]
        return found

    def adopt(self, repo: str) -> Optional[set[int]]:
        """The repository's open pull requests that the operator wrote or that request the operator by login,
        and every one adopted before that is still open, or None when the repository does not exist.

        GitHub drops a review request once the login reviews, so a pull request stays adopted after."""
        folder = cache_dir(self.root, repo)
        record = read_json(folder / "adopted.json") or {}
        adopted = {number for number in record.get("numbers") or [] if isinstance(number, int)}
        closed = {number for number in record.get("closed") or [] if isinstance(number, int)}
        operator = (self.daemon.operator or "").lower()
        if not operator:
            return adopted
        found: set[int] = set()
        page = 1
        while True:
            path = folder / "open" / f"{page}.json"
            cached = read_json(path) or {}
            response = self.daemon.conditional(f"repos/{repo}/pulls?state=open&per_page={PAGE}&page={page}",
                                               cached.get("etag"))
            body = response.json()
            if response.status == 200 and isinstance(body, list):
                pulls = [open_pull(item) for item in body if isinstance(item, dict)]
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json(path, {"etag": response.etag, "pulls": pulls})
            elif response.status == 304 and isinstance(cached.get("pulls"), list):
                pulls = cached["pulls"]
            elif response.status == 404 and page == 1:
                return None
            else:
                return adopted
            found |= {pull["number"] for pull in pulls if isinstance(pull.get("number"), int)
                      and ((pull.get("author") or "").lower() == operator
                           or operator in [(person or "").lower() for person in pull.get("requested") or []])}
            if len(pulls) < PAGE:
                break
            page += 1
        if found - closed - adopted:
            adopted |= found - closed
            folder.mkdir(parents=True, exist_ok=True)
            write_json(folder / "adopted.json", {"numbers": sorted(adopted), "closed": sorted(closed)})
        return adopted

    def claims(self, lines: dict[str, list[Subscription]], adopted: dict[str, set[int]]
               ) -> tuple[dict[tuple[tuple[str, int], str], dict[str, Claim]], dict[tuple[str, int], str]]:
        """Each pull request and role a live session's line covers, with each session's best claim on it, and
        the spelling of each pull request's repository."""
        roles: dict[tuple[tuple[str, int], str], dict[str, Claim]] = {}
        spellings: dict[tuple[str, int], str] = {}
        for name, found in lines.items():
            added = self.states[name].data["added"]
            excluded = {((line.repo.lower(), line.number), line.role_name) for line in found if line.excluded}
            covered: dict[str, set[int]] = {}
            for line in found:
                if line.excluded:
                    continue
                numbers = [line.number] if line.number is not None else sorted(adopted.get(line.repo.lower(), ()))
                for number in numbers:
                    pull = (line.repo.lower(), number)
                    if (pull, line.role_name) in excluded:
                        continue
                    if line.number is None:
                        covered.setdefault(line.repo.lower(), set()).add(number)
                    claim = Claim(name, line, line.rank(added.get(line.key, 0.0)))
                    bucket = roles.setdefault((pull, line.role_name), {})
                    if name not in bucket or claim.rank > bucket[name].rank:
                        bucket[name] = claim
                    if line.number is not None or pull not in spellings:
                        spellings[pull] = line.repo
            self.states[name].data["adopted"] = {repo: sorted(numbers) for repo, numbers in sorted(covered.items())}
        return roles, spellings

    # Ask and query

    def refresh(self, pull: tuple[str, int], repo: str, fixed: bool) -> object:
        """The pull request's result, queried again when a conditional request says it changed or failed, when
        its base moved and a session fixes it, while its mergeability is unknown, and when a full query is due.
        A failed query carries the last result forward. Answers GONE when GitHub says it does not exist."""
        number = pull[1]
        path = pull_cache(self.root, repo, number)
        entry = read_json(path) or {}
        etags = entry.get("etags") if isinstance(entry.get("etags"), dict) else {}
        pending = dict(etags)
        dirty = not pull_request(entry)
        probe = self.daemon.conditional(f"repos/{repo}/pulls/{number}", etags.get("pull"))
        if probe.status == 404:
            return GONE
        if probe.status == 200:
            pending["pull"] = probe.etag
        dirty |= probe.status != 304
        reviews = entry.get("reviews") if isinstance(entry.get("reviews"), list) else None
        page = len(reviews or []) // PAGE + 1
        known = etags.get("reviews") if isinstance(etags.get("reviews"), dict) else {}
        probe = self.daemon.conditional(f"repos/{repo}/pulls/{number}/reviews?per_page={PAGE}&page={page}",
                                        known.get("etag") if known.get("page") == page else None)
        if probe.status == 200:
            pending["reviews"] = {"page": page, "etag": probe.etag}
        reviews_changed = probe.status != 304
        dirty |= reviews_changed
        pr = pull_request(entry)
        if fixed and pr.get("baseRefName"):
            base = self.branch(repo, pr["baseRefName"])
            dirty |= base is None or base != etags.get("base")
        dirty |= pr.get("mergeable") == "UNKNOWN"
        fetched = seconds_of(entry.get("fetched_at"))
        full = fetched is None or self.now - fetched >= self.settings.full_every
        if not (dirty or full):
            return entry
        repository = self.daemon.query(repo, number, self.now)
        if repository is None:
            return entry if pr else None
        if reviews_changed or full or reviews is None:
            read = self.daemon.read_reviews(repo, number)
            if read is None:
                pending["reviews"] = etags.get("reviews")
            else:
                reviews = read
        base_ref = (repository.get("pullRequest") or {}).get("baseRefName")
        if fixed and base_ref:
            pending["base"] = self.branch(repo, base_ref)
        entry = {"fetched_at": stamp(self.now), "etags": {key: value for key, value in pending.items() if value},
                 "repository": repository, "reviews": reviews or []}
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, entry)
        return entry

    def branch(self, repo: str, base: str) -> Optional[str]:
        """The base branch's ETag, GONE when the branch does not exist, or None when the request failed."""
        key = (repo.lower(), base)
        if key not in self.branches:
            path = cache_dir(self.root, repo) / "branches" / f"{quote(base, safe='')}.json"
            cached = read_json(path) or {}
            probe = self.daemon.conditional(f"repos/{repo}/git/ref/heads/{quote(base, safe='/')}", cached.get("etag"))
            value: Optional[str] = None
            if probe.status == 200 and probe.etag:
                value = probe.etag
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json(path, {"etag": value})
            elif probe.status == 304:
                value = cached.get("etag")
            elif probe.status == 404:
                value = GONE
                path.unlink(missing_ok=True)
            self.branches[key] = value
        return self.branches[key]

    # Gone, merged, and closed

    def lose_pull(self, pull: tuple[str, int], repo: str, adopted: dict[str, set[int]]) -> None:
        target = f"{repo}#{pull[1]}"
        for name in self.states:
            if remove(self.root, name, repo, pull[1], None):
                self.tell(name, f"MISSING {target}: no such repository or pull request, or the operator cannot see "
                                "it, so the watch removed its subscription — tell the user")
        self.forget_adopted(repo, pull[1], adopted)
        pull_cache(self.root, repo, pull[1]).unlink(missing_ok=True)

    def lose_repository(self, repo: str, lines: dict[str, list[Subscription]]) -> None:
        for name, found in lines.items():
            removed = []
            for line in found:
                if line.role == "review" and line.number is None and not line.excluded \
                        and line.repo.lower() == repo.lower():
                    removed += remove(self.root, name, line.repo, None, "review", line.model)
            if removed:
                self.tell(name, f"MISSING {repo}: no such repository, or the operator cannot see it, so the watch "
                                "removed its subscription — tell the user")
            lines[name] = [line for line in found if line.text not in removed]

    def forget_adopted(self, repo: str, number: int, adopted: dict[str, set[int]]) -> None:
        folder = cache_dir(self.root, repo)
        record = read_json(folder / "adopted.json")
        if record is not None:
            numbers = [found for found in record.get("numbers") or [] if found != number]
            closed = sorted({*(record.get("closed") or []), number})
            write_json(folder / "adopted.json", {"numbers": numbers, "closed": closed})
        adopted.get(repo.lower(), set()).discard(number)

    def close(self, pull: tuple[str, int], roles: dict, owners: dict[str, str], entry: dict, repo: str,
              adopted: dict[str, set[int]]) -> None:
        """Announce MERGED or CLOSED once to each owner, then remove the pull request's lines from every
        session, leaving its `!` lines, so its subscriptions end."""
        pr = pull_request(entry)
        number = pull[1]
        ref = f"{repo}#{number}"
        if pr.get("state") == "MERGED":
            when = f" at {pr['mergedAt']}" if pr.get("mergedAt") else ""
            oid = (pr.get("mergeCommit") or {}).get("oid")
            commit = f" (merge commit {oid[:8]})" if oid else ""
            detail = f"MERGED {ref} into {pr.get('baseRefName') or '?'}{when}{commit}"
        else:
            detail = f"CLOSED {ref} without merging"
        held: dict[str, list[str]] = {}
        for (found, name), claims in sorted(roles.items()):
            owner = owners.get(role_label(found, name))
            if found == pull and owner in claims:
                held.setdefault(owner, []).append(name)
        for session, names in held.items():
            closed = self.states[session].data["closed"]
            if pull_label(pull) not in closed:
                closed[pull_label(pull)] = self.now
                self.tell(session, f"{detail} — {closing_actions(repo, number, names)}")
        for name in session_names(self.root):
            remove(self.root, name, repo, number, None)
        for state in self.states.values():
            state.data["fix"].pop(role_label(pull, "fix"), None)
            for label in [label for label in state.data["review"] if label_pull(label)[0] == pull]:
                del state.data["review"][label]
        for label in [label for label in owners if label_pull(label)[0] == pull]:
            del owners[label]
        self.forget_adopted(repo, number, adopted)

    # Owners

    def choose(self, roles: dict, spellings: dict[tuple[str, int], str]) -> dict[str, str]:
        previous = {label: owner for label, owner in (read_json(self.root / "owners.json") or {}).items()
                    if isinstance(owner, str)}
        self.release_lapsed(previous, spellings)
        owners = {}
        for (pull, name), bucket in sorted(roles.items()):
            label = role_label(pull, name)
            current = previous.get(label)
            desired = max(bucket.values(), key=lambda claim: (claim.rank, claim.session)).session
            repo = spellings[pull]
            if name == "fix":
                owner = self.move_fix(pull, repo, current, desired)
            else:
                owner = self.move_review(pull, name, repo, current, desired)
            owners[label] = owner
        return owners

    def release_lapsed(self, previous: dict[str, str], spellings: dict[tuple[str, int], str]) -> None:
        """Clear at once every lock a lapsed owner's reviews hold: its reviewer went with its conversation."""
        for label, owner in previous.items():
            pull, name = label_pull(label)
            if name.startswith("review ") and owner not in self.daemon.live:
                for lock in self.locks_for(spellings.get(pull, pull[0]), pull[1]):
                    if lock.loop == owner:
                        self.clear(pull, lock)

    def move_fix(self, pull: tuple[str, int], repo: str, current: Optional[str], desired: str) -> str:
        if current == desired:
            return current
        if current in self.daemon.live:
            self.tell(current, f"MOVED {repo}#{pull[1]} fix to another session — stop subagent fix-{pull[1]}")
        self.gain(desired, pull, "fix")
        return desired

    def move_review(self, pull: tuple[str, int], name: str, repo: str, current: Optional[str], desired: str) -> str:
        """Move a review role only between reviews: while a lock naming the owner has not stalled, the owner
        keeps the role. A stalled lock goes, and the role moves to the session that outranks the owner, or
        stays with the owner, which hears that its last review stalled."""
        number = pull[1]
        model = name.split(" ", 1)[1]
        mine = [lock for lock in self.locks_for(repo, number)
                if current is not None and lock.model_key == key_of(model) and lock.loop == current]
        stalled = bool(mine) and all(self.daemon.stalled(lock, self.mono) for lock in mine)
        if current == desired:
            if stalled:
                for lock in mine:
                    self.clear(pull, lock)
                self.stalled_roles.add((pull, name))
            return current
        if current in self.daemon.live:
            if mine and not stalled:
                return current
            for lock in mine:
                self.clear(pull, lock)
            self.tell(current, f"MOVED {repo}#{number} review for {model} to another session — stop subagent "
                               f"review-{number} and run no project review finish")
        self.gain(desired, pull, name)
        if current is not None:
            try:
                review.copy_published(current, desired, repo, number, root=self.daemon.review_root)
            except (OSError, ValueError, ProjectorError) as error:
                self.daemon.log(f"could not copy {current}'s record of {repo}#{number} to {desired}: {error}")
        return desired

    def gain(self, session: str, pull: tuple[str, int], name: str) -> None:
        """Forget what the daemon announced to a new owner about the role, so it hears what is outstanding."""
        state = self.states[session].data
        if name == "fix":
            state["fix"].pop(role_label(pull, "fix"), None)
        else:
            state["review"].pop(role_label(pull, name), None)

    def locks_for(self, repo: str, number: int) -> list:
        pull = (repo.lower(), number)
        if pull not in self.lock_lists:
            try:
                found = list(review.review_locks(repo, number, root=self.daemon.review_root))
            except (OSError, ProjectorError) as error:
                self.daemon.log(f"could not read the review locks of {repo}#{number}: {error}")
                found = []
            for lock in found:
                self.daemon.observe(lock, self.mono)
            self.lock_lists[pull] = found
        return self.lock_lists[pull]

    def clear(self, pull: tuple[str, int], lock: "review.ReviewLock") -> None:
        review.clear_lock(lock)
        self.lock_lists[pull] = [found for found in self.lock_lists.get(pull, []) if found.path != lock.path]
        self.daemon.locks.pop(lock.path, None)

    # Announce

    def route(self, roles: dict, owners: dict[str, str], results: dict, spellings: dict) -> set[tuple[str, int]]:
        """Append each owner's new events and due reminders, and answer the pull requests that merged or closed."""
        reviewers: dict[tuple[str, int], set[str]] = {}
        for (pull, name) in roles:
            if name != "fix" and owners.get(role_label(pull, name)):
                reviewers.setdefault(pull, set()).add(owners[role_label(pull, name)])
        closing = set()
        for (pull, name), bucket in sorted(roles.items()):
            owner = owners.get(role_label(pull, name))
            claim = bucket.get(owner) if owner else None
            pr = pull_request(results.get(pull))
            if claim is None or not pr:
                continue
            if pr.get("state") != "OPEN":
                closing.add(pull)
                continue
            if name == "fix":
                lines = self.route_fix(owner, pull, spellings[pull], pr, results[pull].get("reviews") or [],
                                       reviewers.get(pull, set()))
            else:
                lines = self.route_review(owner, pull, name, spellings[pull], results[pull]["repository"],
                                          claim.line)
            self.tell(owner, *lines)
        return closing

    def route_fix(self, session: str, pull: tuple[str, int], repo: str, pr: dict, reviews: list[dict],
                  reviewers: set[str]) -> list[str]:
        """What a `fix` owner should hear, kept level-triggered as `watch-threads.sh` was: each piece of work
        still outstanding is announced when it is new and again every `watch.renotify` seconds."""
        number = pull[1]
        ref = f"{repo}#{number}"
        label = role_label(pull, "fix")
        announced = self.states[session].data["fix"].get(label) or {}
        kept: dict[str, float] = {}
        lines: list[str] = []
        action = f"run fix-pr on {ref} in subagent fix-{number}"

        def standing(key: str, first: str, again: Optional[str]) -> None:
            last = announced.get(key)
            if last is None:
                lines.append(first)
                last = self.now
            elif again is not None and self.now - last >= self.settings.renotify:
                lines.append(again)
                last = self.now
            kept[key] = last

        head = pr.get("headRefOid") or ""
        if pr.get("isDraft") and session not in reviewers:
            if reviewers:
                unreviewed, wait = "", "wait for its review"
            else:
                unreviewed, wait = ", and no session on this machine reviews it", \
                    f"tell the user that no session reviews {ref}"
            standing(f"draft:{number}:{head}",
                     f"DRAFT {ref} head={head[:8]}: no review has signed off this head{unreviewed}; the work, if any, "
                     f"is in its threads and review bodies — {wait}",
                     f"DRAFT {ref} (still open) head={head[:8]}: still not signed off{unreviewed} — {wait}")
        conflict = f"conflict:{number}:{head}"
        mergeable = pr.get("mergeable") or "UNKNOWN"
        if mergeable == "UNKNOWN" and conflict in announced:
            kept[conflict] = announced[conflict]
        elif mergeable == "CONFLICTING":
            base = pr.get("baseRefName") or "?"
            standing(conflict,
                     f"CONFLICT {ref} head={head[:8]} base={base}: the branch conflicts with its base; merge the base "
                     f"and resolve it — {action}",
                     f"CONFLICT {ref} (still open) head={head[:8]} base={base}: still conflicts with its base — {action}")
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            for node in nodes(pr, "changesRequested"):
                sha = (node.get("commit") or {}).get("oid") or "?"
                who = login(node) or "?"
                said = snippet(re.sub(r"[\r\n\t]+", " ", node.get("body") or ""))
                standing(f"verdict:{who}:{sha}",
                         f"VERDICT {ref} CHANGES_REQUESTED on {sha[:8]} [{who}]{said} — {action}",
                         f"VERDICT {ref} (still open) CHANGES_REQUESTED on {sha[:8]} [{who}]{said} — {action}")
        for thread in nodes(pr, "reviewThreads"):
            if thread.get("isResolved") or not thread.get("id"):
                continue
            last = (nodes(thread, "last") or [{}])[-1]
            place = f"{thread.get('path')}:{thread.get('line') or '?'}"
            who = login(last) or "?"
            said = snippet(re.sub(r"[\r\n\t]+", " ", HTML_COMMENT.sub("", last.get("body") or "")).lstrip(" "))
            standing(thread["id"], f"FINDING {ref} {place} [{who}]{said} — {action}",
                     f"FINDING {ref} (still open) {place} [{who}]{said} — {action}")
        for found in reviews:
            if found.get("state") != "COMMENTED" or found.get("id") is None:
                continue
            body = found.get("body") or ""
            text = re.sub(r"[\r\n]+", " ", HTML_TAG.sub("", HTML_COMMENT.sub("", body)))
            if not text.strip():
                continue
            counted = review.SUGGESTIONS_COUNT.search(body)
            count = int(counted.group(1)) if counted else 0
            listing = "" if count == 0 else f", listing {count} suggestion{'' if count == 1 else 's'}"
            who = (found.get("user") or {}).get("login") or "?"
            sha = (found.get("commit_id") or "?")[:8]
            standing(f"review:{found['id']}", f"REVIEW {ref} by [{who}] on {sha}{listing}{snippet(text)} — {action}",
                     None)
        self.states[session].data["fix"][label] = kept
        return lines

    def route_review(self, session: str, pull: tuple[str, int], name: str, repo: str, repository: dict,
                     line: Subscription) -> list[str]:
        """What a `review` owner should hear, as `watch-prs.sh` said it, with a reminder while the head waits
        on its review and the stalled review's NEW HEAD."""
        pr = repository["pullRequest"]
        number = pull[1]
        ref = f"{repo}#{number}"
        model = name.split(" ", 1)[1]
        reviewer = (line.reviewer or "").lower()
        label = role_label(pull, name)
        record = self.states[session].data["review"].get(label) or {}
        head = pr.get("headRefOid") or ""
        branch = pr.get("headRefName") or "?"
        action = f"run review-pr on {ref} in subagent review-{number} with --loop {session}"
        verdicts = head_verdicts(pr, head, repository.get("visibility"))
        reviewed = any(found["login"] == reviewer and found["model"] == model for found in verdicts)
        counted = sorted((found for found in verdicts if found["counted"]), key=lambda found: found["at"])
        newest = counted[-1]["verdict"] if counted else None
        unresolved = [thread for thread in nodes(pr, "reviewThreads") if not thread.get("isResolved")]
        answer = newest_answer(unresolved)
        key = f"{head}:{answer if answer is not None else '-'}"
        known = record.get("head")
        flag = record.get("flag") or "-"
        announced = record.get("announced")
        if known is None and reviewed:
            known = head
        lines = []
        if (pull, name) in self.stalled_roles and not reviewed:
            lines.append(f"NEW HEAD {ref} ({branch}) head={head[:7]}, its last review stalled — stop subagent "
                         f"review-{number}, then {action}")
        elif known is None:
            lines.append(f"NEW PR {ref} ({branch}) head={head[:7]}: unreviewed, needs an exact-head review — {action}")
        elif known != head:
            lines.append(f"NEW HEAD {ref} ({branch}): {known[:7]} -> {head[:7]}, needs an exact-head review — {action}")
        if lines:
            known, flag, announced = head, key, self.now
        if is_held(pr):
            flag = "held"
        elif not lines and known == head:
            waiting = None
            if not unresolved or answer is not None:
                author = login(pr).lower()
                operator = (self.daemon.operator or "").lower()
                if pr.get("isDraft") and (not operator or author == operator):
                    waiting = "draft"
                elif newest == "changes-requested":
                    waiting = "changes-requested"
            if waiting is None:
                flag = "-"
            else:
                if not unresolved:
                    seen, now_key, how = flag.split(":")[0], head, "with every thread resolved"
                else:
                    seen, now_key, how = flag, key, "and the author replied on an open finding"
                if flag == "held":
                    how = "now that its hold is lifted"
                if seen != now_key:
                    asked = (f"changes requested {how}; re-review this head, and a clean one is a COMMENT, never an "
                             "approval") if waiting == "changes-requested" else \
                        f"still a draft {how}; re-review this head and sign off if clean"
                    lines.append(f"RESPONDED {ref} ({branch}) head={head[:7]}: {asked} — run review-pr on {ref} with "
                                 f"--rereview in subagent review-{number} with --loop {session}")
                    flag = key
        if reviewed:
            announced = None
        elif not lines and announced is not None and self.now - announced >= self.settings.renotify \
                and not self.in_flight(repo, number, repository, model, reviewer, head):
            lines.append(f"NEW HEAD {ref} (still open) ({branch}) head={head[:7]}: still needs an exact-head review "
                         f"— {action}")
            announced = self.now
        self.states[session].data["review"][label] = {"head": known, "flag": flag, "announced": announced}
        return lines

    def in_flight(self, repo: str, number: int, repository: dict, model: str, reviewer: str, head: str) -> bool:
        """Whether a review of the head by the model is under way: on this machine, a lock for the head that
        has not stalled; from another, a live start comment that this machine's review state does not record."""
        for lock in self.locks_for(repo, number):
            if lock.model_key == key_of(model) and lock.sha == head and not self.daemon.stalled(lock, self.mono):
                return True
        canonical = repository.get("nameWithOwner") or repo
        try:
            paths = review.Paths(self.daemon.review_root or review.state_dir(), canonical, number, model)
            recorded = ((read_json(paths.state) or {}).get("start_comment") or {}).get("id")
        except ProjectorError:
            recorded = None
        pr = repository["pullRequest"]
        reviews = [{"body": node.get("body") or "", "at": node.get("submittedAt")} for node in nodes(pr, "reviews")]
        at = datetime.fromtimestamp(self.now, timezone.utc)
        for comment in nodes(pr, "comments"):
            body = comment.get("body") or ""
            marker = review.START_MARKER.search(body)
            if login(comment).lower() != reviewer or marker is None or marker.group(1) != head \
                    or comment.get("databaseId") == recorded or not comment.get("createdAt"):
                continue
            named = marker.group(2) or next(iter(review.START_MODEL.findall(body)), None)
            if named is None or review.identity(named) != model:
                continue
            start = {"sha": head, "model": named, "created_at": comment["createdAt"],
                     "updated_at": comment.get("updatedAt") or comment["createdAt"]}
            if review.is_live(start, reviews, at):
                return True
        return False


def key_of(model: str) -> str:
    try:
        return review.model_key(model)
    except ProjectorError:
        return model


def snippet(text: str) -> str:
    text = text[:SNIPPET]
    return f": {text}" if text.strip() else ""


def head_verdicts(pr: dict, head: str, visibility: Optional[str]) -> list[dict]:
    """Every Projector verdict on the head: when, which, by which model and login, and whether it counts as
    `project review status` counts another account's."""
    found = []
    for node in nodes(pr, "reviews"):
        marker = review.verdict_marker(node.get("body") or "")
        if marker is None or marker.sha != head:
            continue
        found.append({"at": node.get("submittedAt") or "", "verdict": marker.verdict,
                      "model": review.identity(marker.model), "login": login(node).lower(),
                      "counted": review.review_counts(node.get("authorAssociation"), visibility)})
    return found


def newest_answer(unresolved: list[dict]) -> Optional[int]:
    """The newest reply by the author on an open Projector finding: the id of a last comment that carries
    neither the finding marker nor the reviewer's verify marker."""
    answers = []
    for thread in unresolved:
        first = (nodes(thread, "first") or [{}])[0]
        if review.FINDING_MARKER not in (first.get("body") or ""):
            continue
        last = (nodes(thread, "last") or nodes(thread, "first"))[-1]
        if not ANSWERED.search(last.get("body") or ""):
            answers.append(last.get("databaseId") or 0)
    return max(answers) if answers else None


def is_held(pr: dict) -> bool:
    """Whether the pull request's author has a hold comment open on it; anyone else's holds nothing."""
    author = login(pr).lower()
    return bool(author) and any(login(comment).lower() == author and review.HOLD_MARKER in (comment.get("body") or "")
                                for comment in nodes(pr, "comments"))


def closing_actions(repo: str, number: int, names: list[str]) -> str:
    actions = []
    if "fix" in names:
        actions.append(f"finish subagent fix-{number}")
    for name in names:
        if name != "fix":
            model = name.split(" ", 1)[1]
            actions.append(f"have subagent review-{number} run project review finish {number} --repo {repo} "
                           f"--model {model} --force, then finish it")
    return "; ".join(actions)


# The daemon's process

def held(root: Path) -> bool:
    """Whether a daemon holds the lock. Trying it without waiting is the whole check."""
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / "daemon.mutex", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if try_lock(descriptor):
            unlock(descriptor)
            return False
        return True
    finally:
        os.close(descriptor)


def spawn_daemon(root: Path) -> subprocess.Popen:
    """Start `project watch serve` detached from this command, appending its output to `daemon.log`."""
    root.mkdir(parents=True, exist_ok=True)
    detached: dict = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                      if os.name == "nt" else {"start_new_session": True})
    environment = dict(os.environ, XDG_STATE_HOME=str(root.parent.parent))
    with open(root / "daemon.log", "ab") as log:
        return subprocess.Popen([sys.executable, "-m", "projector", "watch", "serve"], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, close_fds=True, env=environment, **detached)


def wait_for_release(root: Path, sleep: Callable[[float], None], mono: Callable[[], float]) -> bool:
    deadline = mono() + STOP_WAIT
    while held(root):
        if mono() > deadline:
            return False
        sleep(0.1)
    return True


def ensure_daemon(root: Path, version: str, newer: Callable[[str, str], bool], *,
                  spawn: Optional[Callable[[Path], object]] = None, sleep: Callable[[float], None] = time.sleep,
                  mono: Callable[[], float] = time.monotonic) -> str:
    """Start the daemon when none runs, and replace one whose version is strictly older than `version`.

    Answers `started`, `replaced`, or `running`. `newer` compares only versions of dotted numbers, so a
    checkout's `unknown` never replaces a daemon and is never replaced."""
    spawn = spawn or spawn_daemon
    if not held(root):
        spawn(root)
        return "started"
    running = read_json(root / "daemon.json") or {}
    if not newer(version, str(running.get("version") or "")) or not isinstance(running.get("pid"), int):
        return "running"
    write_text(root / "stop", f"{running['pid']}\n")
    if not wait_for_release(root, sleep, mono):
        return "running"
    spawn(root)
    return "replaced"


def stop_daemon(root: Path, *, sleep: Callable[[float], None] = time.sleep,
                mono: Callable[[], float] = time.monotonic) -> Optional[int]:
    """Ask the running daemon to exit and wait up to 10 seconds; answer its pid, or None when none ran."""
    if not held(root):
        return None
    pid = (read_json(root / "daemon.json") or {}).get("pid")
    if not isinstance(pid, int):
        raise ProjectorError(f"a daemon holds {root / 'daemon.mutex'}, and {root / 'daemon.json'} names no pid")
    write_text(root / "stop", f"{pid}\n")
    if not wait_for_release(root, sleep, mono):
        raise ProjectorError(f"the daemon {pid} did not exit within {STOP_WAIT:.0f} seconds")
    return pid


def serve(root: Path, version: str, *, sleep: Callable[[float], None] = time.sleep, **options) -> int:
    """Run the daemon while it holds the lock; a second `serve` finds it held and exits."""
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / "daemon.mutex", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if not try_lock(descriptor):
            print(f"{stamp(time.time())} another daemon holds {root / 'daemon.mutex'}; exiting", flush=True)
            return 0
        try:
            daemon = Daemon(root, version=version, **options)
            daemon.start()
            daemon.log(f"started version {version} as pid {daemon.pid}")
            while daemon.step():
                sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            unlock(descriptor)
    finally:
        os.close(descriptor)
    return 0


# Reports

def status(root: Path, now: float) -> dict:
    daemon = read_json(root / "daemon.json") or {}
    report = read_json(root / "status.json") or {}
    spent = [entry for entry in report.get("graphql") or [] if isinstance(entry, dict)]
    points = sum(entry.get("cost") or 0 for entry in spent if (seconds_of(entry.get("at")) or 0) > now - 3600)
    return {"pid": daemon.get("pid"), "version": daemon.get("version"), "started_at": daemon.get("started_at"),
            "operator": daemon.get("operator"), "live_sessions": report.get("live_sessions") or [],
            "targets": report.get("targets") or [], "repositories": report.get("repositories") or [],
            "last_pass": report.get("last_pass"), "graphql_points_last_hour": points}


def listing(root: Path, session: Optional[str]) -> list[dict]:
    """Each subscription of the session, or of every session, with whether the session is live and which
    roles it owns, as the daemon last recorded them."""
    owners = read_json(root / "owners.json") or {}
    live = set((read_json(root / "status.json") or {}).get("live_sessions") or [])
    rows = []
    for name in [session] if session else session_names(root):
        for line in subscriptions(session_folder(root, name)):
            row = {"session": name, "line": line.text, "target": line.target, "role": line.role,
                   "model": line.model, "reviewer": line.reviewer, "auto": line.auto, "excluded": line.excluded,
                   "live": name in live}
            if not line.excluded and line.number is not None:
                row["owner"] = owners.get(role_label((line.repo.lower(), line.number), line.role_name)) == name
            elif not line.excluded:
                row["owns"] = sorted(label_pull(label)[0][1] for label, owner in owners.items()
                                     if owner == name and label_pull(label)[1] == line.role_name
                                     and label_pull(label)[0][0] == line.repo.lower())
            rows.append(row)
    return rows
