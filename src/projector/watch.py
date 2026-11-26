"""One daemon per machine that watches the pull requests agent sessions fix and review.

A session subscribes by writing lines to its folder under the state root, and every `project watch`
command but `serve` starts the daemon when no process holds its lock. Each pass, the daemon asks
GitHub a conditional question about each pull request a live session subscribes to, runs the GraphQL
query only for one that changed or is due a full query, picks one owner for each pull request and
role, and appends what the owner should do to the owner's `events`. On each full pass it also fetches the
default branch of each repository a live session works in, so configuration reads a merged change to a
committed `.projector.toml` within one full pass. It exits once no session has
been live for five minutes.

Layout of the state root, `$XDG_STATE_HOME/projector/watch/`:

    daemon.mutex                                the lock the running daemon holds
    daemon.json                                 its pid, version, start time, and operator
    daemon.log                                  its output
    stop                                        a pid: the daemon with that pid exits
    status.json                                 live sessions, targets, the last pass, GraphQL spent, and each
                                                checkout's last default-branch fetch
    owners.json                                 the session that owns each pull request and role
    sessions/<session>/subscriptions            one subscription a line
    sessions/<session>/session.json             the deliverer, and when it last renewed the session
    sessions/<session>/events                   event lines not yet delivered
    sessions/<session>/state.json               what the daemon announced to the session, and when
    sessions/<session>/agent.json               its `project agent` registration, live while delivery reaches it
    sessions/<session>/unregistered.json        the mark `project agent unregister` leaves, until it registers again
    cache/<owner>/<repo>/pulls/<number>.json    a pull request's ETags and last result
    cache/<owner>/<repo>/branches/<base>.json   a base branch's ETag
    cache/<owner>/<repo>/open/<page>.json       a page of the repository's open pull requests
    cache/<owner>/<repo>/adopted.json           the pull requests a repository subscription covers
    outbox/<owner>/<repo>.json                  the relay's ref changes waiting for a push (`relay.py`)
    relay/<owner>/<repo>.json                   what the relay last found in the repository

Owner and repository names under `cache/` are lowercased, because GitHub ignores their case.
"""

from __future__ import annotations

import base64
import binascii
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
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional
from urllib.parse import quote

from . import codex_watch, config, review
from .config import MINIMUMS, ConfigError
from .config import load as load_config
from .core import ProjectorError, UsageError
from .inbox import PULL, SESSION, locked, read_json, try_lock, unlock, write_json

LAPSE = 300.0
IDLE = 300.0
SLEPT = 60.0
SETTLE = 60.0
SETTLE_EVERY = 5.0
COMMITTED_EVERY = 600.0
FORGET = 7 * 24 * 3600.0
STOP_WAIT = 10.0
RENEW_EVERY = 30.0
PAGE = 100
# How many seconds after a layer of a stack merges the layer above it can merge into it and still be carried
# with it: GitHub merges a stack's layers about a second apart, from the bottom up.
STACK_MERGE_SPREAD = 10.0
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
CREATED_IN = re.compile(rf"^Creating (?:draft )?pull request for \S+ into \S+ in ({REPO})$")
PUSH_UPDATE = re.compile(r"^\s*\+?\s*[0-9a-f]{4,}\.\.\.?[0-9a-f]{4,}\s+\S+\s+->\s+(\S+)")
GITHUB_REMOTE = re.compile(rf"github\.com[:/]({REPO}?)(?:\.git)?/?$")
STATUS_LINE = re.compile(r"^HTTP/\S+\s+([0-9]{3})\b")
HEADER_END = re.compile(r"\r?\n\r?\n")
HTML_COMMENT = re.compile(r"<!--(?:[^-]|-[^-]|--[^>])*-->")
HTML_TAG = re.compile(r"<[^>]*>")
ANSWERED = re.compile(r"projector-(?:finding|verify)")
PROJECTOR_REVIEW = re.compile(r"^<!-- projector-review ", re.M)

# What each change to a label a review sets tells the `fix` owner, by the label and whether it was added.
LABEL_CHANGES = {
    (review.CLEAN_LABEL, True): "labeled clean, so a review signed off its head — tell the user it is clean",
    (review.CLEAN_LABEL, False): "no longer labeled clean, so its head is not signed off — nothing to do, since any "
                                 "work arrives as its own line",
    (review.REVIEWING_LABEL, True): "labeled reviewing, so a review of it is in progress — nothing to do while it runs",
    (review.REVIEWING_LABEL, False): "no longer labeled reviewing, so no review of it is in progress — nothing to do, "
                                     "since any work arrives as its own line",
}

# The query § 2.6 of the watch-daemon plan gives, which Projector's panel reads from the cache too, with
# the fields the events name: `mergedAt` and `mergeCommit` for MERGED, each comment's id and times for a
# live start comment, `nameWithOwner` for the review state's path, and `defaultBranchRef` for whether a merged
# layer of a stack reached the default branch. The panel reads `labels` for the
# `reviewing` and `clean` labels a review sets, drawing each label in its `color`, and the daemon for their
# LABEL events. `rateLimit` reports what it cost. The panel reads the head's `statusCheckRollup` `state`, and
# the daemon reads the rollup's contexts for CHECKS. GitHub's counts by state cover every context, so they say
# when a failed one lies past the first page. The `projector/review` status is among them, so the query reads
# it too, to take it out of the counts. Each check run's suite says whether its workflow run was cancelled, and
# the check run's start, beside when the run's cancelled jobs ended, says whether the cancel caused its failure.
# The run's workflow and start say whether a newer run of that workflow on the head tests the code again.
# The comments and the head's deployments give `project preview` the Vercel preview it prints, which the
# panel's drawer links: the branch's preview that the `vercel` bot's comment names, or else the
# `environmentUrl` of the head's newest deployment that the `vercel` bot made whose latest status succeeded.
CONTEXT_NODES = ("nodes{ __typename ... on CheckRun{ name conclusion startedAt completedAt checkSuite{ conclusion"
                 " workflowRun{ databaseId createdAt workflow{ databaseId } } } } ... on StatusContext{ context state } }")
QUERY = """query($o:String!,$r:String!,$n:Int!){ rateLimit { cost }
  repository(owner:$o,name:$r){ visibility nameWithOwner defaultBranchRef{ name }
    pullRequest(number:$n){
      number title url body state isDraft reviewDecision mergeable updatedAt mergedAt mergeCommit { oid }
      headRefOid headRefName baseRefName isCrossRepository author { login }
      additions deletions changedFiles
      labels(first:20){ nodes{ name color } }
      commits(last:1){ nodes{ commit{ status{ context(name:%s){ state } }
        statusCheckRollup{ state contexts(first:20){ checkRunCountsByState{ state count }
          statusContextCountsByState{ state count } pageInfo{ hasNextPage endCursor } %s } }
        deployments(last:10){ nodes{ creator{ login } latestStatus{ state environmentUrl } } } } } }
      comments(last:100){ nodes{ databaseId author{ login } authorAssociation body createdAt updatedAt } }
      reviews(last:100){ nodes{ author{ login } authorAssociation submittedAt body } }
      changesRequested: reviews(states:[CHANGES_REQUESTED], last:1){ nodes{ author{ login } body commit{ oid } } }
      reviewThreads(first:100){ nodes{ id isResolved path line
        first: comments(first:1){ nodes{ databaseId body } }
        last: comments(last:1){ nodes{ databaseId author{ login } body } } } } } } }""" % (
    json.dumps(review.REVIEW_CONTEXT), CONTEXT_NODES)

# The head's later rollup contexts, read by commit so that a push between the two queries cannot mix two heads.
CONTEXTS_QUERY = """query($o:String!,$r:String!,$h:GitObjectID!,$c:String!){ rateLimit { cost }
  repository(owner:$o,name:$r){ object(oid:$h){ ... on Commit{ statusCheckRollup{
    contexts(first:100, after:$c){ pageInfo{ hasNextPage endCursor } %s } } } } } }""" % CONTEXT_NODES

# What makes a rollup context a failure for CHECKS. A run that waits for approval, or that was cancelled or
# skipped, says nothing about the code.
CHECK_FAILURES = frozenset({"FAILURE", "TIMED_OUT", "STARTUP_FAILURE"})
STATUS_FAILURES = frozenset({"FAILURE", "ERROR"})


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


def read_adopted(root: Path, repo: str) -> tuple[set[int], set[int]]:
    """The pull requests this machine's reviews of the repository adopted, and those it saw close."""
    record = read_json(cache_dir(root, repo) / "adopted.json") or {}
    numbers, closed = ({number for number in record.get(key) or [] if isinstance(number, int)}
                       for key in ("numbers", "closed"))
    return numbers, closed


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


def request_refresh(root: Path, pulls: Iterable[tuple[str, int]]) -> None:
    """Ask the daemon to query each pull request on its next tick rather than its next pass, as after a push,
    with one file under `refresh/` that the daemon reads and deletes."""
    text = "".join(f"{repo}#{number}\n" for repo, number in pulls)
    if text:
        write_text(root / "refresh" / f"{time.time_ns()}-{os.getpid()}", text)


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
    from . import agents
    with locked(folder):
        before = read_lines(folder)
        after = edit(list(before))
        if after != before:
            write_text(folder / "subscriptions", "".join(f"{line}\n" for line in after))
            with contextlib.suppress(OSError):
                agents.refresh_registration(folder, before, after, time.time())


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
    from . import runtime
    folder = session_folder(root, session)
    with locked(folder):
        if runtime.managed(folder):
            raise UsageError("this session has a managed worker; use Projector's session controls")
        if codex_watch.active(folder, now):
            raise UsageError("this session uses Codex delivery; disconnect it before starting mod or stream")
        write_json(folder / "session.json", {"deliverer": deliverer, "renewed_at": stamp(now)})


def take(root: Path, session: str, now: float) -> list[str]:
    """Empty the session's events and renew it, keeping its deliverer, and answer the lines taken."""
    folder = session_folder(root, session)
    with locked(folder):
        from . import runtime
        if runtime.managed(folder):
            raise UsageError("this session has a managed worker; it owns event delivery")
        if codex_watch.active(folder, now):
            raise UsageError("this session uses Codex delivery; disconnect it before taking its events")
        if codex_watch.registration(folder):
            codex_watch.drain(folder)
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
    folder = session_folder(root, session)
    from . import runtime
    if runtime.managed(folder):
        return "managed" if runtime.live(folder, now) else "none"
    if codex_watch.active(folder, now):
        return "codex"
    record = read_json(folder / "session.json") or {}
    renewed = seconds_of(record.get("renewed_at"))
    if record.get("deliverer") in DELIVERERS and renewed is not None and now - renewed < LAPSE:
        return record["deliverer"]
    return "none"


def append_events(folder: Path, lines: list[str]) -> None:
    with locked(folder):
        from . import runtime
        if runtime.managed(folder) or codex_watch.registration(folder).get("enabled"):
            codex_watch.append(folder, lines)
            return
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
        if events.exists() and events.stat().st_size or codex_watch.registration(events.parent):
            for line in take(root, session, wall()):
                emit(line)
        sleep(1)


# Subscriptions from a command's output

def pushed(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, str]], set[str]]:
    """The pull requests a command's output links, as `gh pr create` prints one, each branch a `git push`
    to GitHub updated, as `(owner/repo, branch)`, and each repository, lowercased, that the output shows a
    push to or a pull request opened in."""
    pulls = [(repo, int(number)) for repo, number in PULL_URL_IN_TEXT.findall(text)]
    branches = []
    targets = set()
    remote: Optional[str] = None
    for line in text.splitlines():
        line = line.rstrip()
        to = PUSH_REMOTE.match(line)
        if to:
            found = GITHUB_REMOTE.search(to.group(1))
            remote = found.group(1) if found else None
            if remote:
                targets.add(remote.lower())
            continue
        created = CREATED_IN.match(line)
        if created:
            targets.add(created.group(1).lower())
        update = PUSH_UPDATE.match(line)
        if update and remote:
            branch = update.group(1).removeprefix("refs/heads/")
            branches.append((remote, branch))
    return pulls, branches, targets


def unique(pairs: list[tuple[str, object]]) -> list[tuple[str, object]]:
    seen: set[tuple[str, object]] = set()
    kept = []
    for repo, value in pairs:
        if (repo.lower(), value) not in seen:
            seen.add((repo.lower(), value))
            kept.append((repo, value))
    return kept


def checked_out(checkout: Path) -> Optional[tuple[str, str, str]]:
    """The checkout's origin repository, its checked-out branch, and its `HEAD`, or None outside a checkout of a
    GitHub repository and on a detached `HEAD`."""
    try:
        repo = review.origin_repo(checkout)
        branch = review.git("-C", str(checkout), "symbolic-ref", "--short", "-q", "HEAD")
        head = review.git("-C", str(checkout), "rev-parse", "HEAD")
    except review.ReviewError:
        return None
    return (repo, branch, head) if branch and head else None


def add_from_output(root: Path, session: str, text: str, checkout: Path) -> tuple[list[str], list[str], bool]:
    """Subscribe the session to `fix auto` on each pull request the output opened or pushed to, ask the
    daemon to refresh each one, and answer the lines added, a problem for each pull request that could not be
    looked up, and whether the session now subscribes to any of those pull requests, through a line added or
    one it already had.

    A linked pull request counts only in a repository the output pushed to or opened a pull request in, or
    the checkout's origin, so a link to another repository, or an example in help text, adds nothing. Each
    one the session does not already hold must be open on GitHub, which one REST request confirms.

    Output that names no pull request and no pushed branch, as a push's does when a pipe such as `| tail -2`
    cut its lines or `--quiet` printed none, falls back to the checkout: the open pull request from its
    checked-out branch counts when its head is the checkout's `HEAD`, as it is right after the push. The
    output cannot say which branch a quiet push named, so a quiet push of another branch counts that pull
    request too while the checked-out branch is in sync with it."""
    pulls, branches, targets = pushed(text)
    if any(repo.lower() not in targets for repo, _ in pulls):
        with contextlib.suppress(review.ReviewError):
            targets.add(review.origin_repo(checkout).lower())
    lines = [line for line in subscriptions(session_folder(root, session)) if line.role == "fix"]
    held = {(line.repo.lower(), line.number) for line in lines if not line.excluded}
    dropped = {(line.repo.lower(), line.number) for line in lines if line.excluded}
    found: list[tuple[str, object]] = []
    problems = []
    for repo, number in unique([(repo, number) for repo, number in pulls if repo.lower() in targets]):
        key = (repo.lower(), number)
        if key in dropped:
            continue
        if key not in held:
            result = run_gh(["api", f"repos/{repo}/pulls/{number}"])
            pull = json_of(result)
            if not isinstance(pull, dict):
                problems.append(f"could not look up {repo}#{number}: "
                                f"{(result.stderr or result.stdout).strip() or 'gh failed'}")
                continue
            if str(pull.get("state", "")).lower() != "open":
                continue
        found.append((repo, number))
    for repo, branch in unique(branches):
        owner = repo.split("/")[0]
        result = run_gh(["api", f"repos/{repo}/pulls?head={quote(f'{owner}:{branch}', safe=':')}&state=open"])
        listed = json_of(result)
        if not isinstance(listed, list):
            problems.append(f"could not look up the pull request for {repo} {branch}: "
                            f"{(result.stderr or result.stdout).strip() or 'gh failed'}")
            continue
        found += [(repo, pull["number"]) for pull in listed if isinstance(pull, dict)
                  and isinstance(pull.get("number"), int)]
    current = None if pulls or branches else checked_out(checkout)
    if current is not None:
        repo, branch, head = current
        owner = repo.split("/")[0]
        result = run_gh(["api", f"repos/{repo}/pulls?head={quote(f'{owner}:{branch}', safe=':')}&state=open"])
        listed = json_of(result)
        if isinstance(listed, list):
            found += [(repo, pull["number"]) for pull in listed if isinstance(pull, dict)
                      and isinstance(pull.get("number"), int) and (pull.get("head") or {}).get("sha") == head]
        else:
            problems.append(f"could not look up the pull request for {repo} {branch}: "
                            f"{(result.stderr or result.stdout).strip() or 'gh failed'}")
    added = []
    refreshed = []
    for repo, number in unique(found):
        try:
            line = add(root, session, Subscription(repo, number, "fix", auto=True))
        except Excluded:
            continue
        refreshed.append((repo, number))
        if line:
            added.append(line)
    request_refresh(root, refreshed)
    return added, problems, bool(refreshed)


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


def signed_in(gh: Callable[[list[str]], GhResult]) -> Optional[str]:
    """The login `gh` is signed in as, or None when `gh api user` names none."""
    result = gh(["api", "user"])
    try:
        found = json.loads(result.stdout).get("login") if result.returncode == 0 else None
    except (ValueError, AttributeError):
        found = None
    return found if isinstance(found, str) and found else None


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
            "body": found.get("body") or "", "commit_id": found.get("commit_id"), "html_url": found.get("html_url"),
            "submitted_at": found.get("submitted_at"), "author_association": found.get("author_association")}


def standing_suggestions(reviews: list[dict], head: str, operator: str, visibility: Optional[str],
                         also: tuple[str, ...] = ()) -> dict:
    """How many suggestions each review lists, for the reviews whose suggestions `project review suggestions`,
    run by the operator, lists on `head`: each account's newest Projector verdict on it by each model, and the
    full review an incremental one among them builds on, which can be of an earlier head. `reviews` are the
    pull request's reviews as `trim_review` keeps them."""
    rows = [{"id": found.get("id"), "url": found.get("html_url"), "at": found.get("submitted_at"),
             "login": (found.get("user") or {}).get("login"), "association": found.get("author_association"),
             "body": found.get("body") or ""} for found in reviews if found.get("id") is not None]
    chosen = review.suggesting_reviews(head, review.counted_reviews(rows, operator, visibility, also))
    return {found["id"]: review.listed_suggestions(found) for found in chosen}


def open_pull(found: dict, repo: str) -> dict:
    """What a pass keeps of an open pull request from the repository's list: who wrote it, whose review it
    requests, and its branches. A head branch in a fork is kept as None, since no layer here builds on it."""
    head = found.get("head") or {}
    own = ((head.get("repo") or {}).get("full_name") or "").lower() == repo.lower()
    return {"number": found.get("number"), "author": (found.get("user") or {}).get("login"),
            "requested": [(person or {}).get("login") for person in found.get("requested_reviewers") or []],
            "base": (found.get("base") or {}).get("ref"), "head": head.get("ref") if own else None}


def nodes(value: Optional[dict], key: str) -> list[dict]:
    return [node for node in ((value or {}).get(key) or {}).get("nodes") or [] if isinstance(node, dict)]


def login(node: Optional[dict]) -> str:
    return ((node or {}).get("author") or {}).get("login") or ""


def head_commit(pr: dict) -> dict:
    return (nodes(pr, "commits") or [{}])[-1].get("commit") or {}


def failures(contexts: list[dict]) -> list[dict]:
    """The failed check runs and commit statuses among `contexts`, as the rollup's counts count them, less the
    `projector/review` status, which a changes-requested verdict posts as `failure` and the VERDICT and FINDING
    events report."""
    return [found for found in contexts
            if (found.get("__typename") == "CheckRun" and found.get("conclusion") in CHECK_FAILURES)
            or (found.get("__typename") == "StatusContext" and found.get("state") in STATUS_FAILURES
                and found.get("context") != review.REVIEW_CONTEXT)]


def workflow_run(found: dict) -> Optional[tuple[int, str]]:
    """The workflow and start of the GitHub Actions run a check run belongs to, or None for a check run that no
    workflow run made."""
    run = (found.get("checkSuite") or {}).get("workflowRun") or {}
    workflow, at = (run.get("workflow") or {}).get("databaseId"), run.get("createdAt")
    return (workflow, at) if isinstance(workflow, int) and isinstance(at, str) else None


def run_id(found: dict) -> Optional[int]:
    """The id of the workflow run a check run belongs to, or None for a check run that no workflow run made."""
    run = (found.get("checkSuite") or {}).get("workflowRun") or {}
    return run.get("databaseId") if isinstance(run.get("databaseId"), int) else None


def cancel_times(contexts: list[dict]) -> dict[int, str]:
    """When each cancelled workflow run among `contexts` was cancelled: the earliest end of its check runs that
    concluded `CANCELLED`. A cancelled run none of whose cancelled check runs was read has no time here."""
    times: dict[int, str] = {}
    for found in contexts:
        run, ended = run_id(found), found.get("completedAt")
        if found.get("__typename") != "CheckRun" or run is None or not isinstance(ended, str) \
                or found.get("conclusion") != "CANCELLED" \
                or (found.get("checkSuite") or {}).get("conclusion") != "CANCELLED":
            continue
        times[run] = min(times.get(run, ended), ended)
    return times


def caused_by_cancel(found: dict, times: dict[int, str]) -> bool:
    """Whether a failed check run of a cancelled workflow run started at or after the cancel, which only a job
    that runs `if: always()` to gather the jobs it needs does: it starts once they end, so in a cancelled run it
    starts after the cancel and fails though nothing in the code did. A failure that started before the cancel
    is the code's, and so is one in a run whose cancel time is unknown."""
    run, started = run_id(found), found.get("startedAt")
    cancelled = times.get(run) if run is not None else None
    return ((found.get("checkSuite") or {}).get("conclusion") == "CANCELLED" and cancelled is not None
            and isinstance(started, str) and started >= cancelled)


def superseded(found: dict, contexts: list[dict]) -> bool:
    """Whether a check run belongs to a cancelled workflow run that a newer run of the same workflow on the head
    tests again: one still running, or one whose check run of the same name ran rather than skipped or was
    cancelled. A newer run in the same concurrency group cancels the one in progress, as a review's label does
    where CI starts on `labeled`, and GitHub keeps the cancelled run's checks on the head, where a job that runs
    `if: always()` to gather the others fails though nothing in the code did. The newer run's own result stands
    for the code. A cancelled run with nothing newer, or whose newer run skipped the check, keeps each failure
    that the cancel did not cause, since that check failed before the cancel."""
    run = workflow_run(found)
    if run is None or (found.get("checkSuite") or {}).get("conclusion") != "CANCELLED":
        return False
    for other in contexts:
        newer = workflow_run(other) if other.get("__typename") == "CheckRun" else None
        if newer is None or newer[0] != run[0] or newer[1] <= run[1]:
            continue
        if (other.get("checkSuite") or {}).get("conclusion") is None:
            return True
        if other.get("name") == found.get("name") and other.get("conclusion") not in {"SKIPPED", "CANCELLED"}:
            return True
    return False


def failed_contexts(contexts: list[dict]) -> list[str]:
    """The names of the failures among `contexts` that CHECKS reports: each but a check run of a cancelled run
    that the cancel caused or that a newer run tests again."""
    times = cancel_times(contexts)
    return [found.get("name") or found.get("context") or "?" for found in failures(contexts)
            if not caused_by_cancel(found, times) and not superseded(found, contexts)]


def failure_count(commit: dict) -> int:
    """How many of the head's rollup contexts failed by the rollup's counts, which cover every page, less a
    failed `projector/review` status."""
    contexts = (commit.get("statusCheckRollup") or {}).get("contexts") or {}

    def counted(key: str, states: frozenset) -> int:
        return sum(row["count"] for row in contexts.get(key) or []
                   if isinstance(row, dict) and row.get("state") in states and isinstance(row.get("count"), int))

    own = ((commit.get("status") or {}).get("context") or {}).get("state")
    return (counted("checkRunCountsByState", CHECK_FAILURES) + counted("statusContextCountsByState", STATUS_FAILURES)
            - (own in STATUS_FAILURES))


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


# The default branches configuration reads

def live_checkouts(root: Path, live: set[str]) -> dict[str, Path]:
    """One checkout of each repository a live registered session works in, by the repository's common Git
    directory, from the `cwd` each session's `agent.json` records."""

    found: dict[str, Path] = {}
    for name in sorted(live):
        cwd = (read_json(session_folder(root, name) / "agent.json") or {}).get("cwd")
        tree = config.working_tree(Path(cwd).resolve()) if isinstance(cwd, str) and cwd else None
        if tree is not None:
            found.setdefault(str(tree.common), tree.top)
    return found


def fetch_default(top: Path) -> dict:
    """Fetch the default branch of the repository at `top` into Projector's own ref under `DEFAULT_REFS`.

    The empty `--refmap=` stops Git from also moving the remote-tracking ref
    `remote.origin.fetch` maps the branch to, so the fetch changes no ref you
    use, `git status` says nothing new, and a fetch of yours never finds
    `refs/remotes/origin/*` locked. It fetches no tags and no submodules for
    the same reason.
    """

    from . import relay
    tree = config.working_tree(top)
    branch = config.default_branch(tree, config.cache_dir()) if tree is not None else None
    if branch is None or not branch.name.startswith("origin/"):
        return {"state": "skipped", "error": "no origin/HEAD names a default branch"}
    name = branch.name.removeprefix("origin/")
    try:
        done = relay.git(top, "fetch", "--quiet", "--no-write-fetch-head", "--no-tags", "--no-recurse-submodules",
                         "--refmap=", "origin", f"+refs/heads/{name}:{config.DEFAULT_REFS}origin/{name}")
    except relay.RelayError as error:
        return {"state": "error", "error": str(error)}
    if done.returncode:
        return {"state": "error", "error": done.stderr.decode("utf-8", "replace").strip()}
    return {"state": "ok", "error": ""}


@dataclass(frozen=True)
class ReviewSettings:
    """What a repository's configuration says about who reviews it: the logins `review.reviewers` names,
    whether `review.ci` says GitHub Actions reviews its pull requests, and whether `review.automatic` leaves
    them to be reviewed, as they are unless it is `false`."""

    reviewers: tuple[str, ...] = ()
    ci: bool = False
    automatic: bool = True


def review_settings(found: config.Config) -> ReviewSettings:
    return ReviewSettings(review.extra_reviewers(found.setting("review.reviewers")), found.setting("review.ci") is True,
                          found.setting("review.automatic") is not False)


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
        # Pull requests a refresh request named: those the next pass queries whatever their ETags say, and those
        # it polls every `SETTLE_EVERY` seconds, until the daemon's clock reaches each one's time, while GitHub
        # still computes whether they can merge.
        self.forced: set[tuple[str, int]] = set()
        self.settling: dict[tuple[str, int], float] = {}
        self.last_pass: Optional[float] = None
        self.codex = codex_watch.Dispatcher(wall=wall)
        from . import relay
        self.relay = relay.Relay(root, gh=self.gh, wall=wall, log=self.log)
        # Each repository's running fetch of its default branch, by common Git directory, with the checkout it
        # runs in, and the last result for each checkout.
        self.fetching: dict[str, tuple[Path, Future]] = {}
        self.fetched: dict[str, dict] = {}
        self.last_fetch: Optional[float] = None
        # By pull request, when GitHub's stacks were last asked about it, and the bottom layer of its stack.
        self.stacks: dict[tuple[str, int], tuple[float, Optional[int]]] = {}
        # By repository, lowercased, when its committed `.projector.toml` was last read from GitHub, and the
        # review settings read then.
        self.committed: dict[str, tuple[float, ReviewSettings]] = {}

    def log(self, message: str) -> None:
        print(f"{stamp(self.wall())} {message}", flush=True)

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_daemon()
        self.relay.start()
        self.read_operator(self.wall())

    def write_daemon(self) -> None:
        write_json(self.root / "daemon.json", {"pid": self.pid, "version": self.version,
                                               "started_at": stamp(self.started_at), "operator": self.operator,
                                               "runtime_protocol": 1})

    def read_operator(self, now: float) -> None:
        """Read the login `gh` is signed in as at the start and on each full pass, and drop every ETag when it
        changes, because GitHub varies an ETag with the token."""
        if self.operator is not None and self.operator_read is not None \
                and now - self.operator_read < self.settings.full_every:
            return
        found = signed_in(self.gh)
        if found is None:
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
            from . import runtime
            runtime.coordinate(self.root)
            self.scan(now)
            self.take_refreshes(now)
            if self.live:
                self.last_live = now
            elif now - self.last_live >= IDLE and not self.relay.busy() and not self.fetching:
                self.log("stopping: no session has been live for 5 minutes")
                return False
            polled = self.wanted or now >= self.next_pass
            if polled:
                self.run_pass()
            changed = self.relay.tick(self.live, polled, self.settings.full_every)
            if self.fetch_defaults(polled) or changed:
                self.write_status(self.wall())
            self.codex.tick([self.root / "sessions" / name for name in session_names(self.root)])
            return True
        finally:
            self.last_tick = self.mono()

    def fetch_defaults(self, polled: bool) -> bool:
        """Read finished fetches, and on a full pass, every `full_every` seconds, start a fetch of the default
        branch of each repository a live session works in, on the relay's worker pool, unless the checkout's
        configuration sets `watch.fetch_default` to false. Answers whether a result changed."""
        changed = False
        for common, (top, future) in list(self.fetching.items()):
            if future.done():
                del self.fetching[common]
                try:
                    result = future.result()
                except Exception as error:
                    result = {"state": "error", "error": f"{type(error).__name__}: {error}"}
                self.fetched[str(top)] = {**result, "at": stamp(self.wall())}
                changed = True
        now = self.wall()
        if not polled or (self.last_fetch is not None and now - self.last_fetch < self.settings.full_every):
            return changed
        self.last_fetch = now
        for common, top in live_checkouts(self.root, self.live).items():
            if common in self.fetching:
                continue
            try:
                wanted = load_config(top).setting("watch.fetch_default") is not False
            except ConfigError as error:
                self.fetched[str(top)] = {"state": "error", "error": str(error), "at": stamp(now)}
                changed = True
                continue
            if wanted:
                self.fetching[common] = (top, self.relay.pool.submit(fetch_default, top))
        return changed

    def forgive(self, now: float) -> None:
        """Count every live session as seen, and every lock as changed, now: a gap in the loop is the machine
        sleeping, which should lapse no session and stall no review."""
        for name in self.live:
            if name in self.sessions:
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
        live.update(name for name in names if codex_watch.active(self.root / "sessions" / name, wall)
                    and codex_watch.has_work(self.root / "sessions" / name))
        live.update(name for name in names if (self.root / "sessions" / name / "agent.json").exists()
                    and delivery(self.root, name, wall) != "none")
        for name in names:
            mark = file_signature(self.root / "sessions" / name / "subscriptions")
            if name in live and mark != self.marks.get(name) and self.lacks_result(name):
                self.wanted = True
            self.marks[name] = mark
        if live - self.live:
            self.wanted = True
        self.live = live

    def take_refreshes(self, now: float) -> None:
        """Read and delete each refresh request, and ask for a pass at once that queries the pull requests it
        names. A request older than `SETTLE`, written while no daemon ran, is deleted unread."""
        folder = self.root / "refresh"
        try:
            paths = sorted(path for path in folder.iterdir() if not path.name.startswith("."))
        except OSError:
            return
        wall = self.wall()
        for path in paths:
            try:
                fresh = wall - path.stat().st_mtime < SETTLE
                text = path.read_text(encoding="utf-8", errors="replace") if fresh else ""
                path.unlink()
            except OSError:
                continue
            for line in text.splitlines():
                match = PULL.match(line.strip())
                if match:
                    pull = (match["repo"].lower(), int(match["number"]))
                    self.forced.add(pull)
                    self.settling[pull] = now + SETTLE
                    self.wanted = True

    def settle(self, results: dict, now: float) -> None:
        """Keep polling each open pull request a refresh named, every `SETTLE_EVERY` seconds for up to
        `SETTLE`, while GitHub reports its mergeability as unknown, as it does for a while after a push."""
        def unknown(pr: dict) -> bool:
            return pr.get("state") == "OPEN" and (pr.get("mergeable") or "UNKNOWN") == "UNKNOWN"

        self.settling = {pull: until for pull, until in self.settling.items()
                         if now < until and unknown(pull_request(results.get(pull)))}
        if self.settling:
            self.next_pass = min(self.next_pass, now + SETTLE_EVERY)

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
        forced, self.forced = self.forced, set()
        done = Pass(self, now, now_mono, forced)
        done.run()
        self.settle(done.results, now_mono)
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
            "relay": self.relay.status(),
            "default_branches": dict(sorted(self.fetched.items())),
        })

    def conditional(self, path: str, etag: Optional[str]) -> Response:
        args = ["api", "-i", *(["-H", f"If-None-Match: {etag}"] if etag else []), path]
        return parse_response(self.gh(args).stdout)

    def graphql(self, query: str, now: float, *fields: str) -> Optional[dict]:
        """The data a GraphQL query answers, or None when it failed. Its cost counts either way."""
        result = self.gh(["api", "graphql", "-f", f"query={query}", *fields])
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        data = payload.get("data") or {}
        if data:
            self.spent.append((now, ((data.get("rateLimit") or {}).get("cost")) or 1))
        return None if result.returncode != 0 or payload.get("errors") else data

    def query(self, repo: str, number: int, now: float) -> Optional[dict]:
        owner, name = repo.split("/")
        data = self.graphql(QUERY, now, "-f", f"o={owner}", "-f", f"r={name}", "-F", f"n={number}") or {}
        repository = data.get("repository")
        if not isinstance(repository, dict) or not isinstance(repository.get("pullRequest"), dict):
            return None
        self.read_contexts(owner, name, repository["pullRequest"], now)
        return repository

    def read_contexts(self, owner: str, name: str, pr: dict, now: float) -> None:
        """Add the head's later rollup contexts to the first page while the rollup's counts hold a failed context
        that the pages read so far lack. A page that fails leaves the contexts read before it."""
        head, commit = pr.get("headRefOid"), head_commit(pr)
        contexts = (commit.get("statusCheckRollup") or {}).get("contexts")
        if not head or not isinstance(contexts, dict):
            return
        wanted = failure_count(commit)
        read = [found for found in contexts.get("nodes") or [] if isinstance(found, dict)]
        page = contexts.get("pageInfo") or {}
        while len(failures(read)) < wanted and page.get("hasNextPage") and page.get("endCursor"):
            data = self.graphql(CONTEXTS_QUERY, now, "-f", f"o={owner}", "-f", f"r={name}", "-f", f"h={head}",
                                "-f", f"c={page['endCursor']}") or {}
            paged = (data.get("repository") or {}).get("object") or {}
            later = (paged.get("statusCheckRollup") or {}).get("contexts")
            if not isinstance(later, dict) or not later.get("nodes"):
                break
            read += [found for found in later.get("nodes") or [] if isinstance(found, dict)]
            page = later.get("pageInfo") or {}
        contexts["nodes"] = read
        contexts["pageInfo"] = page

    def read_stack(self, repo: str, number: int) -> Optional[dict]:
        """The GitHub stack that holds the pull request, or None when GitHub lists none or cannot answer."""
        result = self.gh(["api", f"repos/{repo}/stacks?pull_request={number}"])
        found = json_of(result)
        return found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None

    def stack(self, repo: str, number: int, stack: Optional[dict], now: float) -> Optional[int]:
        """The bottom layer of `stack` when it is an open stack of two or more that holds the pull request,
        recorded for each of the stack's layers, or else None. A stack lists a merged layer still, so its bottom
        stays the bottom."""
        layers = [layer["number"] for layer in stack_layers(stack)]
        bottom = layers[0] if (stack or {}).get("open", True) and len(layers) > 1 and number in layers else None
        for layer in layers if bottom is not None else []:
            self.stacks[repo.lower(), layer] = (now, bottom)
        return bottom

    def read_reviews(self, repo: str, number: int) -> Optional[list[dict]]:
        result = self.gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews?per_page={PAGE}"])
        found = decode_pages(result.stdout) if result.returncode == 0 else None
        return None if found is None else [trim_review(item) for item in found if isinstance(item, dict)]

    def committed_settings(self, repo: str, now: float) -> ReviewSettings:
        """`repo`'s review settings from the `.projector.toml` its default branch commits on GitHub, never a pull
        request's head, read at most once every `COMMITTED_EVERY` seconds of the daemon's clock. A repository
        without the file has none. A read that fails logs and keeps the settings read before, or none."""
        key = repo.lower()
        held = self.committed.get(key)
        if held is not None and now - held[0] < COMMITTED_EVERY:
            return held[1]
        settings = held[1] if held is not None else ReviewSettings()
        response = self.conditional(f"repos/{repo}/contents/{config.CONFIG_NAME}", None)
        found = response.json() if response.status == 200 else None
        try:
            if response.status == 404:
                settings = review_settings(config.load_committed(None, ""))
            elif isinstance(found, dict) and found.get("encoding") == "base64" \
                    and isinstance(found.get("content"), str):
                data = base64.b64decode(found["content"])
                settings = review_settings(config.load_committed(data, f"{repo}:{config.CONFIG_NAME}"))
            else:
                self.log(f"could not read {repo}'s {config.CONFIG_NAME} from GitHub"
                         f"{f' (HTTP {response.status})' if response.status else ''}; keeping its last review settings")
        except (binascii.Error, ConfigError) as error:
            self.log(f"could not read {repo}'s {config.CONFIG_NAME} from GitHub: {error}")
        self.committed[key] = (now, settings)
        return settings

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
    """The daemon's record for one session: when it first saw each line, what it announced, and when, and the
    review labels each pull request it fixes carried."""

    KEYS = ("added", "fix", "review", "closed", "adopted", "labels")

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

    def __init__(self, daemon: Daemon, now: float, now_mono: float,
                 forced: Optional[set[tuple[str, int]]] = None) -> None:
        self.daemon = daemon
        self.forced = forced or set()
        self.results: dict[tuple[str, int], dict] = {}
        self.root = daemon.root
        self.settings = daemon.settings
        self.now = now
        self.mono = now_mono
        self.states = {name: SessionState(daemon.root / "sessions" / name) for name in sorted(daemon.live)}
        self.outbox: dict[str, list[str]] = {}
        self.branches: dict[tuple[str, str], Optional[str]] = {}
        self.lock_lists: dict[tuple[str, int], list] = {}
        self.stalled_roles: set[tuple[tuple[str, int], str]] = set()
        self.stall_keys: dict[tuple[tuple[str, int], str], list] = {}
        # By repository, what `adopt` found, and the review targets of the operator's sessions on other machines.
        self.covering: dict[str, Optional[set[int]]] = {}
        self.remote: dict[str, Optional[list[list[dict]]]] = {}
        # By repository, its open pull requests, and by pull request, the subagent that fixes it.
        self.listings: dict[str, object] = {}
        self.fixers: dict[tuple[str, int], str] = {}
        # By pull request, the GitHub stack that holds it, and by repository and branch, the pull requests whose
        # head it is, each read at most once a pass.
        self.github_stacks: dict[tuple[str, int], Optional[dict]] = {}
        self.headed: dict[tuple[str, str], Optional[dict]] = {}
        # By repository, lowercased, its review settings, as `settings_of` reads them.
        self.repo_settings: Optional[dict[str, ReviewSettings]] = None

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
        self.covering.update(adopted)
        roles, spellings = self.claims(lines, adopted)
        fixed = {pull for pull, name in roles if name == "fix"}
        results = self.results
        for pull, spelling in sorted(spellings.items()):
            result = self.refresh(pull, spelling, pull in fixed)
            if result is GONE:
                self.lose_pull(pull, spelling, adopted)
            else:
                results[pull] = result
        roles = {role: claims for role, claims in roles.items() if role[0] in results}
        self.daemon.stacks = {pull: found for pull, found in self.daemon.stacks.items() if pull in results}
        owners = self.choose(roles, spellings, results)
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
            for role in ("fix", "review", "labels"):
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
        adopted, closed = read_adopted(self.root, repo)
        operator = (self.daemon.operator or "").lower()
        if not operator:
            return adopted
        pulls = self.open_pulls(repo)
        if pulls is GONE:
            return None
        if not isinstance(pulls, list):
            return adopted
        found = {pull["number"] for pull in pulls if isinstance(pull.get("number"), int)
                 and ((pull.get("author") or "").lower() == operator
                      or operator in [(person or "").lower() for person in pull.get("requested") or []])}
        if found - closed - adopted:
            adopted |= found - closed
            folder.mkdir(parents=True, exist_ok=True)
            write_json(folder / "adopted.json", {"numbers": sorted(adopted), "closed": sorted(closed)})
        return adopted

    def open_pulls(self, repo: str) -> object:
        """The repository's open pull requests as `open_pull` keeps them, read once a pass by conditional
        requests for its cached pages, None when a request failed, or GONE when the repository does not exist."""
        name = repo.lower()
        if name in self.listings:
            return self.listings[name]
        folder = cache_dir(self.root, repo)
        found: list[dict] = []
        page = 1
        while True:
            path = folder / "open" / f"{page}.json"
            cached = read_json(path) or {}
            kept = cached.get("pulls")
            # A page cached before the list kept each pull request's branches is read again whole.
            current = isinstance(kept, list) and all(isinstance(pull, dict) and "base" in pull for pull in kept)
            response = self.daemon.conditional(f"repos/{repo}/pulls?state=open&per_page={PAGE}&page={page}",
                                               cached.get("etag") if current else None)
            body = response.json()
            if response.status == 200 and isinstance(body, list):
                pulls = [open_pull(item, repo) for item in body if isinstance(item, dict)]
                path.parent.mkdir(parents=True, exist_ok=True)
                write_json(path, {"etag": response.etag, "pulls": pulls})
            elif response.status == 304 and current:
                pulls = kept
            else:
                self.listings[name] = GONE if response.status == 404 and page == 1 else None
                return self.listings[name]
            found += pulls
            if len(pulls) < PAGE:
                break
            page += 1
        self.listings[name] = found
        return found

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
        its base moved and a session fixes it, while its mergeability is unknown, when a refresh request named
        it, and when a full query is due. A failed query carries the last result forward. Answers GONE when
        GitHub says it does not exist."""
        number = pull[1]
        path = pull_cache(self.root, repo, number)
        entry = read_json(path) or {}
        etags = entry.get("etags") if isinstance(entry.get("etags"), dict) else {}
        pending = dict(etags)
        dirty = not pull_request(entry) or pull in self.forced
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
        detail = None
        held: dict[str, list[str]] = {}
        for (found, name), claims in sorted(roles.items()):
            owner = owners.get(role_label(found, name))
            if found == pull and owner in claims:
                held.setdefault(owner, []).append(name)
        for session, names in held.items():
            closed = self.states[session].data["closed"]
            if pull_label(pull) not in closed:
                closed[pull_label(pull)] = self.now
                if detail is None:
                    detail = self.closed_detail(ref, number, repo, entry)
                fixer = self.fixer(pull, repo, pr) if "fix" in names else ""
                suggestions = self.merged_suggestions(repo, entry) if "fix" in names else None
                self.tell(session, f"{detail} — {closing_actions(repo, number, names, fixer, suggestions)}")
        for name in session_names(self.root):
            remove(self.root, name, repo, number, None)
        for state in self.states.values():
            state.data["fix"].pop(role_label(pull, "fix"), None)
            for label in [label for label in state.data["review"] if label_pull(label)[0] == pull]:
                del state.data["review"][label]
        for label in [label for label in owners if label_pull(label)[0] == pull]:
            del owners[label]
        self.forget_adopted(repo, number, adopted)

    def merged_suggestions(self, repo: str, entry: dict) -> Optional[int]:
        """How many suggestions `project review suggestions` lists on a merged pull request's last head, as
        `REVIEW` counts them, or None when it closed without merging and its suggestions went with it."""
        pr = pull_request(entry)
        if pr.get("state") != "MERGED":
            return None
        visibility = (entry.get("repository") or {}).get("visibility")
        return sum(standing_suggestions(entry.get("reviews") or [], pr.get("headRefOid") or "",
                                        self.daemon.operator or "", visibility, self.counted_logins(repo)).values())

    def closed_detail(self, ref: str, number: int, repo: str, entry: dict) -> str:
        pr = pull_request(entry)
        if pr.get("state") != "MERGED":
            return f"CLOSED {ref} without merging"
        when = f" at {pr['mergedAt']}" if pr.get("mergedAt") else ""
        oid = (pr.get("mergeCommit") or {}).get("oid")
        commit = f" (merge commit {oid[:8]})" if oid else ""
        base = pr.get("baseRefName") or "?"
        default = ((entry.get("repository") or {}).get("defaultBranchRef") or {}).get("name")
        landed = self.landed(repo, number, pr.get("mergedAt"), base, default) \
            if default and base not in ("?", default) else None
        into = f"with its stack into {default}" if landed else f"into {base}"
        behind = f", which is not in {default} yet" if landed is False else ""
        return f"MERGED {ref} {into}{when}{commit}{behind}"

    def landed(self, repo: str, number: int, merged_at: object, base: str, default: str) -> Optional[bool]:
        """Whether the merged pull request's change reached the default branch through its base: True once each
        step down the chain of base branches reaches the default branch, False when a pull request below is open
        or merged before the change reached it, None when GitHub does not say. Each step takes the pull request
        whose head is the base from GitHub's stack, or else from the pull requests with that head, and it
        carried the change only when it merged no earlier than the one above it, less the seconds GitHub
        spreads a stack's merge over. A stack layer's base is the head of the layer below it, until a step
        shows it was retargeted after that layer merged, and then GitHub says where it merged."""
        stack = self.github_stack(repo, number)
        layers = {(layer.get("head") or {}).get("ref"): layer for layer in stack_layers(stack)}
        heads = list(layers)
        ordered = dict(zip(heads[1:], heads))
        if heads:
            ordered[heads[0]] = ((stack or {}).get("base") or {}).get("ref")
        when, upper, assumed, seen = seconds_of(merged_at), None, False, set()
        while base != default:
            if not base or when is None or (base, assumed) in seen:
                return None
            seen.add((base, assumed))
            below = layers.get(base) or self.pull_headed(repo, base)
            merged = seconds_of((below or {}).get("merged_at"))
            if below is not None and below.get("state") == "open":
                return False
            if merged is None:
                return None
            if merged < when - STACK_MERGE_SPREAD:
                if not assumed:
                    return False
                base, assumed = ((self.pull_headed(repo, upper) or {}).get("base") or {}).get("ref"), False
                continue
            when, upper = merged, base
            if base in layers:
                base, assumed = ordered[base], True
            else:
                base, assumed = (below.get("base") or {}).get("ref"), False
        return True

    def github_stack(self, repo: str, number: int) -> Optional[dict]:
        """The GitHub stack that holds the pull request, asked at most once a pass for all the stack's layers."""
        pull = (repo.lower(), number)
        if pull not in self.github_stacks:
            stack = self.daemon.read_stack(repo, number)
            for layer in [number, *(found["number"] for found in stack_layers(stack))]:
                self.github_stacks[repo.lower(), layer] = stack
        return self.github_stacks[pull]

    def pull_headed(self, repo: str, branch: str) -> Optional[dict]:
        """The pull request whose head is `branch`: an open one, or else one that merged, or None when there is
        neither or GitHub cannot answer. GitHub is asked at most once a pass for each branch."""
        key = (repo.lower(), branch)
        if key not in self.headed:
            owner = repo.split("/")[0]
            result = self.daemon.gh(["api", f"repos/{repo}/pulls?head={quote(f'{owner}:{branch}', safe=':')}"
                                            "&state=all"])
            found = json_of(result)
            pulls = [pull for pull in found if isinstance(pull, dict)] if isinstance(found, list) else []
            self.headed[key] = next((pull for pull in pulls if pull.get("state") == "open"),
                                    next((pull for pull in pulls if pull.get("merged_at")), None))
        return self.headed[key]

    # Owners

    def choose(self, roles: dict, spellings: dict[tuple[str, int], str], results: dict) -> dict[str, str]:
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
                owner = self.move_fix(pull, repo, pull_request(results.get(pull)), current, desired)
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

    def move_fix(self, pull: tuple[str, int], repo: str, pr: dict, current: Optional[str], desired: str) -> str:
        if current == desired:
            return current
        if current in self.daemon.live:
            self.tell(current, f"MOVED {repo}#{pull[1]} fix to another session — stop subagent "
                               f"{self.fixer(pull, repo, pr)}")
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
                self.stall_keys[pull, name] = [(str(lock.path), lock.modified) for lock in mine]
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

    def settings_of(self, repo: str) -> ReviewSettings:
        """`repo`'s `review.reviewers`, `review.ci`, and `review.automatic`, read once a pass from a checkout of it that a live session
        works in, or, when no live session works in one, from the `.projector.toml` its default branch commits on
        GitHub, which the daemon reads again every `COMMITTED_EVERY` seconds."""
        if self.repo_settings is None:
            self.repo_settings = {}
            for top in live_checkouts(self.root, self.daemon.live).values():
                try:
                    self.repo_settings[review.origin_repo(top).lower()] = review_settings(load_config(top))
                except (ProjectorError, ConfigError) as error:
                    self.daemon.log(f"could not read the review settings in {top}: {error}")
        if repo.lower() not in self.repo_settings:
            self.repo_settings[repo.lower()] = self.daemon.committed_settings(repo, self.mono)
        return self.repo_settings[repo.lower()]

    def counted_logins(self, repo: str) -> tuple[str, ...]:
        """The logins whose verdicts count on `repo` beside the operator's, as `project review status` counts
        them there: those `review.reviewers` names."""
        return self.settings_of(repo).reviewers

    def reviewed_in_ci(self, repo: str, pr: dict) -> bool:
        """Whether GitHub Actions reviews the pull request: `review.ci` is on in its repository, and its head is in
        that repository, since the workflow skips a fork's. A failed review there arrives as a failed check."""
        return self.settings_of(repo).ci and not pr.get("isCrossRepository")

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
                                       reviewers.get(pull, set()), results[pull]["repository"].get("visibility"))
            else:
                lines = self.route_review(owner, pull, name, spellings[pull], results[pull]["repository"],
                                          claim.line)
            self.tell(owner, *lines)
        return closing

    def route_fix(self, session: str, pull: tuple[str, int], repo: str, pr: dict, reviews: list[dict],
                  reviewers: set[str], visibility: Optional[str]) -> list[str]:
        """What a `fix` owner should hear, kept level-triggered: each piece of work still outstanding is
        announced when it is new and again every `watch.renotify` seconds. A review is announced once, so its
        key is kept after it stops standing, as a full review's suggestions do while a push waits for the
        incremental review that carries them."""
        number = pull[1]
        ref = f"{repo}#{number}"
        label = role_label(pull, "fix")
        announced = self.states[session].data["fix"].get(label) or {}
        kept: dict[str, float] = {}
        lines: list[str] = []
        action = f"run fix-pr on {ref} in subagent {self.fixer(pull, repo, pr)}"

        def standing(key: str, first: str, again: Optional[str], revision: object = None) -> None:
            last = announced.get(key)
            if last is None:
                lines.append(codex_watch.Event(first, [repo.lower(), number, "fix", key, revision]))
                last = self.now
            elif again is not None and self.now - last >= self.settings.renotify:
                lines.append(codex_watch.Event(again, [repo.lower(), number, "fix", key, revision]))
                last = self.now
            kept[key] = last

        head = pr.get("headRefOid") or ""
        # This machine sees its own sessions, and, through the relay, the review targets of the operator's sessions
        # on other machines. A review running anywhere else shows only as its live start comment, and the event
        # says nothing about a machine it cannot see. Anyone can comment on a public repository, so only an account
        # whose review would count can hold the event back. A repository whose `review.ci` is on reviews each of
        # its own heads in GitHub Actions, and a failed review there arrives as a failed check, so a head from a
        # branch of that repository waits on no session. A repository whose `review.automatic` is `false` expects
        # no review unless someone asks for one, so it waits on no session either. Held back, the head keeps its
        # key, so it is still announced only once. A draft waits on its author, not on a review.
        unreviewed = f"unreviewed:{number}:{head}"
        if not pr.get("isDraft") and not reviewers \
                and not head_sign_off(pr, head, self.daemon.operator or "", visibility,
                                      self.counted_logins(repo))["signed_off"]:
            starts = [{"login": login(comment), "association": comment.get("authorAssociation")}
                      for comment, _ in self.live_starts(pr, head)]
            if not self.settings_of(repo).automatic \
                    or review.counted_reviews(starts, self.daemon.operator or "", visibility,
                                              self.counted_logins(repo)) \
                    or self.reviewed_remotely(repo, number) or self.reviewed_in_ci(repo, pr):
                if unreviewed in announced:
                    kept[unreviewed] = announced[unreviewed]
            else:
                scope = "this machine" if self.remote_reviews(repo) is None else \
                    "this machine or on your other machines that share this repository's relay"
                standing(unreviewed,
                         f"UNREVIEWED {ref} head={head[:8]}: ready for review, its head is not signed off, and no "
                         f"session on {scope} reviews it — tell the user that no session on {scope} reviews {ref}",
                         None)
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
        failed = list(dict.fromkeys(re.sub(r"\s+", " ", name) for name in
                                    failed_contexts(nodes(head_commit(pr).get("statusCheckRollup"), "contexts"))))
        if failed:
            names = ", ".join(failed)
            standing(f"checks:{number}:{head}", f"CHECKS {ref} head={head[:8]} failed: {names} — {action}",
                     f"CHECKS {ref} (still open) head={head[:8]} failed: {names} — {action}", failed)
        if pr.get("reviewDecision") == "CHANGES_REQUESTED":
            for node in nodes(pr, "changesRequested"):
                sha = (node.get("commit") or {}).get("oid") or "?"
                who = login(node) or "?"
                said = snippet(re.sub(r"[\r\n\t]+", " ", node.get("body") or ""))
                standing(f"verdict:{who}:{sha}",
                         f"VERDICT {ref} CHANGES_REQUESTED on {sha[:8]} [{who}]{said} — {action}",
                         f"VERDICT {ref} (still open) CHANGES_REQUESTED on {sha[:8]} [{who}]{said} — {action}",
                         codex_watch.digest(node.get("body") or ""))
        for thread in nodes(pr, "reviewThreads"):
            if thread.get("isResolved") or not thread.get("id"):
                continue
            last = (nodes(thread, "last") or [{}])[-1]
            place = f"{thread.get('path')}:{thread.get('line') or '?'}"
            who = login(last) or "?"
            said = snippet(re.sub(r"[\r\n\t]+", " ", HTML_COMMENT.sub("", last.get("body") or "")).lstrip(" "))
            standing(thread["id"], f"FINDING {ref} {place} [{who}]{said} — {action}",
                     f"FINDING {ref} (still open) {place} [{who}]{said} — {action}",
                     [head, last.get("databaseId"), codex_watch.digest(last.get("body") or "")])
        suggesting = standing_suggestions(reviews, head, self.daemon.operator or "", visibility,
                                          self.counted_logins(repo))
        for found in reviews:
            if found.get("id") is None:
                continue
            body = found.get("body") or ""
            if PROJECTOR_REVIEW.search(body):
                count = suggesting.get(found["id"], 0)
                if count == 0:
                    continue
            elif found.get("state") != "COMMENTED" or found.get("commit_id") != head:
                continue
            else:
                count = 0
            text = re.sub(r"[\r\n]+", " ", HTML_TAG.sub("", HTML_COMMENT.sub("", body)))
            if not text.strip():
                continue
            listing = "" if count == 0 else f", listing {count} suggestion{'' if count == 1 else 's'}"
            who = (found.get("user") or {}).get("login") or "?"
            sha = (found.get("commit_id") or "?")[:8]
            standing(f"review:{found['id']}", f"REVIEW {ref} by [{who}] on {sha}{listing}{snippet(text)} — {action}",
                     None, codex_watch.digest(body))
        for key, last in announced.items():
            if key.startswith("review:"):
                kept.setdefault(key, last)
        self.states[session].data["fix"][label] = kept
        return lines + self.route_labels(session, label, ref, pr, head)

    def fixer(self, pull: tuple[str, int], repo: str, pr: dict) -> str:
        """The subagent that fixes the pull request: `fix-stack-<number>`, named for the bottom layer, for a
        layer of a stack, or else `fix-<number>`. A fix to a layer is carried up into each layer above it, so
        two subagents on one stack would push the same branches."""
        if pull not in self.fixers:
            bottom = self.bottom(pull, repo, pr)
            self.fixers[pull] = f"fix-stack-{bottom}" if bottom is not None else f"fix-{pull[1]}"
        return self.fixers[pull]

    def bottom(self, pull: tuple[str, int], repo: str, pr: dict) -> Optional[int]:
        """The bottom layer of the stack that holds the pull request, or None when it is in none. GitHub's
        stack answers, asked once each full pass for all its layers, and a chain of base branches answers where
        GitHub records none. A pull request the open ones show in no chain costs no request, unless it was in a
        stack before. Once named, a bottom holds until GitHub names another or the chain reaches below it: a
        merged bottom drops out of the chain, and a lookup that fails or finds no stack names no other, so
        neither gives a layer a second fixer."""
        number = pull[1]
        pulls = self.open_pulls(repo)
        listed = [found for found in pulls if isinstance(found.get("number"), int)] if isinstance(pulls, list) else []
        bases = {found["number"]: found.get("base") for found in listed}
        heads = {found["head"]: found["number"] for found in listed if found.get("head")}
        own = next((found for found in listed if found["number"] == number), None)
        base = own.get("base") if own else pr.get("baseRefName")
        head = own.get("head") if own else pr.get("headRefName")
        chained = base in heads or head is not None and head in bases.values()
        cached = self.daemon.stacks.get(pull)
        known = cached[1] if cached is not None else None
        fresh = cached is not None and self.now - cached[0] < self.settings.full_every
        if fresh and known is not None:
            return known
        asked = cached[0] if fresh else None
        if not fresh and (chained or not isinstance(pulls, list) or known is not None):
            asked = self.now
            bottom = self.daemon.stack(repo, number, self.github_stack(repo, number), self.now)
            if bottom is not None:
                return bottom
        bottom, seen = (number if chained else None), {number}
        while chained and base in heads and heads[base] not in seen:
            bottom = heads[base]
            seen.add(bottom)
            base = bases[bottom]
        if known is not None and (bottom is None or known not in seen):
            bottom = known
        if asked is not None:
            self.daemon.stacks[pull] = (asked, bottom)
        return bottom

    def remote_reviews(self, repo: str) -> Optional[list[list[dict]]]:
        """The review targets of the operator's live sessions on other machines that share the repository's relay,
        one list for each session, or None when the relay is not on for the repository."""
        name = repo.lower()
        if name not in self.remote:
            from . import relay
            self.remote[name] = None if self.daemon.relay.key is None or not self.daemon.operator else \
                relay.remote_reviews(self.root, repo, self.daemon.operator, self.now)
        return self.remote[name]

    def reviewed_remotely(self, repo: str, number: int) -> bool:
        """Whether a session on another machine reviews the pull request: by its number, or by a review of the
        repository that covers it as `adopt` decides for this machine's sessions, unless the session dropped the
        pull request from that model's review."""
        pull = f"{repo}#{number}".lower()
        for entries in self.remote_reviews(repo) or []:
            dropped = {review.identity(entry["model"]) for entry in entries
                       if entry.get("excluded") and entry["target"].lower() == pull}
            for entry in entries:
                if entry.get("excluded") or review.identity(entry["model"]) in dropped:
                    continue
                target = entry["target"].lower()
                if target == pull or target == repo.lower() and number in self.covered(repo):
                    return True
        return False

    def covered(self, repo: str) -> set[int]:
        """The pull requests a review of the repository covers, which `adopt` reads once a pass."""
        name = repo.lower()
        if name not in self.covering:
            self.covering[name] = self.adopt(repo)
        return self.covering[name] or set()

    def route_labels(self, session: str, label: str, ref: str, pr: dict, head: str) -> list[str]:
        """A LABEL line for each review label added or removed since the owner last saw the pull request. The
        first look, and the first after the role moves, records the labels and announces nothing, because a line
        reports a change and the labels it finds are none: a subscription to a pull request that is already clean
        reports nothing until the label comes off, on a host without the panel, such as Codex, as on one with it."""
        present = {(node.get("name") or "").lower() for node in nodes(pr, "labels")}
        current = [name for name in review.LABELS if name in present]
        seen = self.states[session].data["labels"].get(label)
        self.states[session].data["labels"][label] = current
        if not isinstance(seen, list):
            return []
        lines = []
        for name in review.LABELS:
            added = name in current
            if added != (name in seen):
                sign = "+" if added else "-"
                lines.append(f"LABEL {ref} {sign}{name} head={head[:8]}: {LABEL_CHANGES[name, added]}")
        return lines

    def route_review(self, session: str, pull: tuple[str, int], name: str, repo: str, repository: dict,
                     line: Subscription) -> list[str]:
        """What a `review` owner should hear, with a reminder while the head waits on its review and the
        stalled review's NEW HEAD."""
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
        signed = head_sign_off(pr, head, reviewer, repository.get("visibility"), self.counted_logins(repo))
        reviewed = any(found["model"] == model and (found["login"] or "").lower() == reviewer
                       for found in signed["models"])
        newest = signed["newest"]["verdict"] if signed["newest"] else None
        unresolved = [thread for thread in nodes(pr, "reviewThreads") if not thread.get("isResolved")]
        answer = newest_answer(unresolved)
        key = f"{head}:{answer if answer is not None else '-'}"
        known = record.get("head")
        flag = record.get("flag") or "-"
        announced = record.get("announced")
        if known is None and reviewed:
            known = head
        if pr.get("isDraft") and line.number is None:
            # A draft's author is still working on it, so a repository subscription waits until it is marked
            # ready, and then announces what no review has read: a pull request never announced, a head newer
            # than the last one known, or, at once, a head announced before it was drafted. A review that stalls
            # meanwhile is reported once, so its subagent still hears to stop.
            lines = []
            if (pull, name) in self.stalled_roles and not reviewed:
                lines.append(f"NEW HEAD {ref} ({branch}) head={head[:7]}, its last review stalled while the pull "
                             f"request is a draft — stop subagent review-{number}, and review it once it is ready")
            self.states[session].data["review"][label] = {
                "head": known, "flag": flag, "announced": None if reviewed or announced is None and not lines else 0}
            return [codex_watch.Event(text, [repo.lower(), number, name, "stalled",
                                            head, self.stall_keys.get((pull, name))]) for text in lines]
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
        if not lines and known == head:
            if newest != "changes-requested" or unresolved and answer is None:
                flag = "-"
            else:
                if not unresolved:
                    seen, now_key, how = flag.split(":")[0], head, "with every thread resolved"
                else:
                    seen, now_key, how = flag, key, "and the author replied on an open finding"
                if seen != now_key:
                    lines.append(f"RESPONDED {ref} ({branch}) head={head[:7]}: changes requested {how}; re-review "
                                 "this head, and a clean one is a COMMENT, never an approval — run review-pr on "
                                 f"{ref} with --rereview in subagent review-{number} with --loop {session}")
                    flag = key
        if reviewed:
            announced = None
        elif not lines and announced is not None and self.now - announced >= self.settings.renotify \
                and not self.in_flight(repo, number, repository, model, reviewer, head):
            lines.append(f"NEW HEAD {ref} (still open) ({branch}) head={head[:7]}: still needs an exact-head review "
                         f"— {action}")
            announced = self.now
        self.states[session].data["review"][label] = {"head": known, "flag": flag, "announced": announced}
        return [codex_watch.Event(text, [repo.lower(), number, name,
                                        "responded" if text.startswith("RESPONDED ") else
                                        "stalled" if "last review stalled" in text else "head",
                                        head, key if text.startswith("RESPONDED ") else
                                        self.stall_keys.get((pull, name)) if "last review stalled" in text else reviewer])
                for text in lines]

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
        return any(login(comment).lower() == reviewer and comment.get("databaseId") != recorded
                   and review.identity(named) == model
                   for comment, named in self.live_starts(repository["pullRequest"], head))

    def live_starts(self, pr: dict, head: str) -> Iterator[tuple[dict, str]]:
        """Each start comment on the pull request that says a review of the head is still under way, on any
        machine, with the model it names."""
        reviews = [{"body": node.get("body") or "", "at": node.get("submittedAt")} for node in nodes(pr, "reviews")]
        at = datetime.fromtimestamp(self.now, timezone.utc)
        for comment in nodes(pr, "comments"):
            body = comment.get("body") or ""
            marker = review.START_MARKER.search(body)
            if marker is None or marker.group(1) != head or not comment.get("createdAt"):
                continue
            named = marker.group(2) or next(iter(review.START_MODEL.findall(body)), None)
            if named is None:
                continue
            start = {"sha": head, "model": named, "created_at": comment["createdAt"],
                     "updated_at": comment.get("updatedAt") or comment["createdAt"]}
            if review.is_live(start, reviews, at):
                yield comment, named


def key_of(model: str) -> str:
    try:
        return review.model_key(model)
    except ProjectorError:
        return model


def snippet(text: str) -> str:
    text = text[:SNIPPET]
    return f": {text}" if text.strip() else ""


def verdict_rows(pr: dict) -> list[dict]:
    """The Projector reviews among the reviews the daemon's query returns, in the shape `review.verdicts` gives,
    so the daemon decides a head's sign-off as `project review publish` does."""
    return [{"id": None, "url": None, "at": node.get("submittedAt"), "login": login(node),
             "association": node.get("authorAssociation"), "body": node.get("body") or ""}
            for node in nodes(pr, "reviews") if review.verdict_marker(node.get("body") or "")]


def head_sign_off(pr: dict, head: str, reviewer: str, visibility: Optional[str],
                  also: tuple[str, ...] = ()) -> dict:
    """`review.sign_off` of the head from the reviewer's verdicts, those of each login in `also`, and those of
    every account `project review status` counts, and the open Projector finding threads in the daemon's
    query."""
    open_threads = sum(1 for thread in nodes(pr, "reviewThreads") if not thread.get("isResolved")
                       and review.FINDING_MARKER in ((nodes(thread, "first") or [{}])[0].get("body") or ""))
    return review.sign_off(head, review.counted_reviews(verdict_rows(pr), reviewer, visibility, also),
                           open_threads)


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


def json_of(result: GhResult) -> object:
    """What a `gh api` call printed, as JSON, or None when it failed or printed something else."""
    try:
        return json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        return None


def stack_layers(stack: Optional[dict]) -> list[dict]:
    """The layers of a GitHub stack, from the bottom up, each with its `number`."""
    return [layer for layer in (stack or {}).get("pull_requests") or []
            if isinstance(layer, dict) and isinstance(layer.get("number"), int)]


def closing_actions(repo: str, number: int, names: list[str], fixer: str, suggestions: Optional[int] = None) -> str:
    """What each role's owner does once the pull request merges or closes. A merged pull request's fixer takes
    the suggestions still owed on it into a follow-up pull request: the `suggestions` its last head lists, which
    a fixer never started for it learns of only here, or else any it deferred on an earlier head, which only the
    fixer remembers. `suggestions` is None for a pull request closed without merging, which owes none. A stack's
    fixer goes on fixing the layers still open, because the user can merge a stack's lower layers first."""
    ref = f"{repo}#{number}"
    stack = fixer.startswith("fix-stack-")

    def finish(who: str) -> str:
        return f"send {who} no more work for {ref}, and finish it once no layer of its stack is open" if stack \
            else f"finish {who}"

    actions = []
    if "fix" in names and suggestions:
        listed = "the suggestion" if suggestions == 1 else f"the {suggestions} suggestions"
        actions.append(f"run fix-pr on {ref} in subagent {fixer} to take {listed} its merged head lists into a "
                       f"follow-up pull request, then {finish('it')}")
    elif "fix" in names and suggestions == 0:
        deferred = f"it deferred on {ref}" if stack else "it deferred"
        actions.append(f"have subagent {fixer} take any suggestion {deferred} into a follow-up pull request, then "
                       f"{finish('it')}")
    elif "fix" in names:
        actions.append(finish(f"subagent {fixer}"))
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
                  mono: Callable[[], float] = time.monotonic, runtime_protocol: int = 0) -> str:
    """Start the daemon when none runs, and replace one whose version is strictly older than `version`.

    Answers `started`, `replaced`, or `running`. `newer` compares only versions of dotted numbers, so a
    checkout's `unknown` never replaces a daemon and is never replaced."""
    spawn = spawn or spawn_daemon
    if not held(root):
        spawn(root)
        return "started"
    running = read_json(root / "daemon.json") or {}
    running_version = str(running.get("version") or "")
    lacks_runtime = running.get("runtime_protocol", 0) < runtime_protocol
    if lacks_runtime and newer(running_version, version):
        raise ProjectorError("The newer watch daemon lacks agent support; upgrade Projector before launching agents")
    if not (newer(version, running_version) or lacks_runtime) or not isinstance(running.get("pid"), int):
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
        daemon = None
        try:
            daemon = Daemon(root, version=version, **options)
            daemon.start()
            daemon.log(f"started version {version} as pid {daemon.pid}")
            while daemon.step():
                sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if daemon is not None:
                daemon.codex.close()
                daemon.relay.close()
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
            "last_pass": report.get("last_pass"), "graphql_points_last_hour": points,
            "relay": report.get("relay") or {"key": {"state": "off", "id": None, "store": None, "error": ""},
                                              "peers": {"count": 0, "error": ""}, "repositories": {}},
            "default_branches": report.get("default_branches") or {},
            "deliveries": {name: found for name in session_names(root)
                           if (found := codex_watch.report(session_folder(root, name), now))}}


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
