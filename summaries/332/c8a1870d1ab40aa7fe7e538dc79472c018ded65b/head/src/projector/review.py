"""The mechanical steps of a review: set up a head, follow a moved head, count
findings, run the repository's gate, and publish.

`review-pr` does its judgment in prose and its bookkeeping here, so the
checks a hand-built helper kept skipping run every time. Every SHA comes from
GitHub or the review's state file, never from an argument; the repository is
passed to `gh` explicitly, so a command runs from any directory; a comment is
posted as the reviewer with a token for that call alone, never by switching
the account the rest of the session uses; and every refusal raises
`ReviewError`, which exits non-zero with what to do about it.

Each model reviewing a pull request is a reviewer of its own, so its state lives
under its model key. Layout of the state directory:

    prs/<owner>/<repo>/<pr>/<model-key>/state.json         the review's state
    prs/<owner>/<repo>/<pr>/<model-key>/<sha>.lock         the lock a review holds from setup until publish
    prs/<owner>/<repo>/<pr>/<model-key>/review.json        the review publish is submitting
    worktrees/<owner>/<repo>/<pr>/<model-key>/<short-sha>  scratch worktrees, never inside the checkout
    loops/<id>/published.json                              a loop's record of the review ids it published
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .core import ProjectorError, distribution_version
from .summary import repo_slug

FINDING_MARKER = "projector-finding"
# The last line of the comment that holds a pull request in draft while changes for it are built, as
# `hold_body` writes it for fix-pr/SKILL.md § Hold the pull request while changes are built.
HOLD_MARKER = "<!-- projector-hold v=1 -->"
# A change a hold comment lists.
HOLD_BULLET = re.compile(r"^[-*]\s+(.+)$")
# The mark that opens both the start comment and the review's signature line, as
# review-pr/SKILL.md § Label every review and finding writes it.
MARK = "📽️"
SHORT = 12
STALE_AFTER = timedelta(days=1)
LOOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# A trailing bracketed variant of a model ID, such as the context window in `claude-opus-5-5[1m]`.
VARIANT = re.compile(r"\[[^\[\]]*\]$")


class ReviewError(ProjectorError):
    """A review step refused; its message says why and what to do."""

    exit_code = 1


class NotFound(ReviewError):
    pass


# GitHub and git

def run_gh(args: list[str], token: Optional[str] = None) -> str:
    """Run gh, with GH_TOKEN set for this call alone when `token` is given."""
    env = None
    if token is not None:
        env = dict(os.environ, GH_TOKEN=token)
    try:
        return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, env=env).stdout
    except FileNotFoundError as exc:
        raise ReviewError("the gh CLI is not installed") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip()
        if "HTTP 404" in detail or "Could not resolve to a" in detail:
            raise NotFound(detail) from exc
        raise ReviewError(f"gh {' '.join(args)} failed: {detail}") from exc


def gh_json(args: list[str], token: Optional[str] = None):
    return json.loads(run_gh(args, token) or "null")


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ReviewError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


def checkout_root(path: Path) -> Path:
    try:
        return Path(git("-C", str(path), "rev-parse", "--show-toplevel")).resolve()
    except ReviewError as exc:
        raise ReviewError(f"{path} is not inside a Git checkout; run this in a checkout of the repository "
                          "or pass --checkout") from exc


def origin_repo(checkout: Path) -> str:
    """The repository origin names, read as configured: `remote get-url` applies a user's
    `insteadOf` rewrites, which can turn the GitHub URL into a mirror's."""
    try:
        repo = repo_slug(git("-C", str(checkout), "config", "--get", "remote.origin.url"))
    except ReviewError:
        repo = ""
    if not repo:
        raise ReviewError(f"{checkout} has no GitHub origin; review from a checkout whose origin is the repository")
    return repo


# Identity

def identity(model: str) -> str:
    """The reviewer a model ID names: the ID lowercased, without a trailing bracketed variant, so one
    model at another context window is the same reviewer."""
    return VARIANT.sub("", model.lower())


def model_key(model: str) -> str:
    """The folder a model's review state lives in: its identity, with every character but a letter, a
    digit, `.`, `_`, and `-` made `-`. Refuses an ID no folder can safely be made from."""
    key = re.sub(r"[^a-z0-9._-]", "-", identity(model))
    if not model or any(c.isspace() for c in model) or not key or key.startswith("."):
        raise ReviewError(f"--model {model!r} is not a usable model ID; pass the ID the host reports for this "
                          "session, such as claude-opus-5-5")
    return key


def authenticated_login() -> str:
    login = run_gh(["api", "user", "--jq", ".login"]).strip()
    if not login:
        raise ReviewError("could not read the authenticated GitHub user; run `gh auth status`")
    return login


def reviewer_login(explicit: Optional[str], configured: Optional[str], operator: str) -> str:
    """The reviewer: explicit input, then review.username, then the authenticated user."""
    for login in (explicit, configured):
        if login:
            return login
    return operator


def reviewer_token(login: str) -> str:
    """A token for the reviewer, for one call; the session's active account is left alone."""
    try:
        token = run_gh(["auth", "token", "--user", login]).strip()
    except ReviewError as exc:
        raise ReviewError(f"no gh login for the reviewer {login}; run `gh auth login` for that account") from exc
    if not token:
        raise ReviewError(f"no gh login for the reviewer {login}; run `gh auth login` for that account")
    return token


# Trust

def trusted_head(repo: str, pr: dict, operator: str) -> tuple[bool, str]:
    """Whether the head's code may run, by review-pr § Run a head's code only when the
    head is trusted, and why not when it may not."""
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != repo:
        return False, f"its head is in {head_repo or 'a deleted fork'}, not {repo}"
    permissions: dict[str, bool] = {}

    def trusted(login: str) -> bool:
        if not login:
            return False
        if login == operator:
            return True
        if login.endswith("[bot]"):
            return False
        if login not in permissions:
            try:
                level = run_gh(["api", f"repos/{repo}/collaborators/{login}/permission",
                                "--jq", ".permission"]).strip()
            except ReviewError:
                level = ""
            permissions[login] = level in ("admin", "maintain", "write")
        return permissions[login]

    author = (pr.get("user") or {}).get("login") or ""
    if not trusted(author):
        return False, f"its author {author or '(unknown)'} has no write access to {repo}"
    number = pr["number"]
    try:
        authors = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/commits",
                          "--jq", '.[] | (.author.login // "")']).splitlines()
    except ReviewError:
        return False, "its commits could not be read"
    for login in authors:
        if not trusted(login.strip()):
            return False, f"a commit names {login.strip() or 'no GitHub user'} as its author"
    return True, ""


# State

def state_dir(explicit: Optional[Path] = None, environ: Optional[dict] = None) -> Path:
    if explicit is not None:
        return explicit
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "projector" / "reviews"


@dataclass
class Paths:
    """Where one model's review of one pull request keeps its state, locks, and worktrees."""

    root: Path
    repo: str
    number: int
    model: str

    @property
    def key(self) -> str:
        return model_key(self.model)

    @property
    def folder(self) -> Path:
        return self.root / "prs" / self.repo / str(self.number) / self.key

    @property
    def state(self) -> Path:
        return self.folder / "state.json"

    @property
    def request(self) -> Path:
        return self.folder / "review.json"

    def lock(self, sha: str) -> Path:
        return self.folder / f"{sha}.lock"

    def locks(self) -> list[Path]:
        return sorted(self.folder.glob("*.lock")) if self.folder.is_dir() else []

    @property
    def worktrees(self) -> Path:
        return self.root / "worktrees" / self.repo / str(self.number) / self.key

    def worktree(self, sha: str) -> Path:
        return self.worktrees / sha[:SHORT]


def loop_record(root: Path, loop: str) -> Path:
    return root / "loops" / loop / "published.json"


def ensure_loop(root: Path, loop: Optional[str]) -> None:
    if loop is None:
        return
    record = loop_record(root, loop)
    if not record.exists():
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("[]\n", encoding="utf-8")


def stored_state(paths: Paths) -> Optional[dict]:
    """The state in the model's folder, refused when it is another model's that shares the folder."""
    if not paths.state.is_file():
        return None
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    if state.get("identity") != identity(paths.model):
        raise ReviewError(f"the review state in {paths.folder} is model {state.get('model')}'s, and model "
                          f"{paths.model} shares its folder; finish that review first with `project review finish "
                          f"{paths.number} --repo {paths.repo} --model {state.get('model')}`")
    return state


def read_state(paths: Paths) -> dict:
    state = stored_state(paths)
    if state is None:
        raise ReviewError(f"no review of {paths.repo}#{paths.number} by model {paths.model} is set up here; "
                          f"run `project review setup {paths.number} --model {paths.model}`")
    return state


def write_state(paths: Paths, state: dict) -> None:
    paths.folder.mkdir(parents=True, exist_ok=True)
    tmp = paths.state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(paths.state)


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(text: str) -> datetime:
    """A time as GitHub gives it, such as `2026-09-27T01:02:03Z`, in UTC."""
    at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def lock_age(lock: Path) -> timedelta:
    try:
        taken = datetime.fromisoformat(json.loads(lock.read_text(encoding="utf-8"))["taken_at"])
    except (OSError, ValueError, KeyError, TypeError):
        taken = datetime.fromtimestamp(lock.stat().st_mtime, timezone.utc)
    return now() - taken


def take_lock(paths: Paths, sha: str, loop: Optional[str]) -> Path:
    """Claim the review of this pull request at this head until publish finishes.

    A lock older than a day is stale and cleared; any other refuses the second instance."""
    lock = paths.lock(sha)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and lock_age(lock) > STALE_AFTER:
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        holder = ""
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
            holder = f" (taken by loop {held.get('loop') or '-'} on {held.get('host')} at {held.get('taken_at')})"
        except (OSError, ValueError):
            pass
        raise ReviewError(
            f"another review of {paths.repo}#{paths.number} at {sha[:7]} by model {paths.model} holds its "
            f"lock{holder}; let it finish, or run `project review release {paths.number} --model {paths.model}` "
            "if no review is running"
        ) from exc
    with os.fdopen(fd, "w") as handle:
        json.dump({"sha": sha, "host": socket.gethostname(), "loop": loop, "pid": os.getpid(),
                   "taken_at": now().isoformat()}, handle)
        handle.write("\n")
    return lock


def release(paths: Paths) -> list[Path]:
    """Clear every lock this model's reviews of the pull request hold; returns what was cleared."""
    released = paths.locks()
    for lock in released:
        lock.unlink(missing_ok=True)
    return released


def gate(paths: Paths, command: Optional[str], output_to_stderr: bool = False) -> dict:
    """Run the repository's `review.gate` command in the review's scratch worktree.

    The command runs the head's code, so it runs only on a head the state file
    records as trusted, and trust is checked first: an untrusted head is refused
    as untrusted whether or not a command is configured, so no refusal points
    the review at the head's code. The command gets the worktree and the merge
    base as `$1` and `$2` and as `PROJECTOR_WORKTREE` and `PROJECTOR_BASE`. Its
    output passes through, its standard output going to this process's
    standard error instead when `output_to_stderr` is set, so a caller printing
    JSON keeps standard output for that alone. Its exit status, with the
    command and when it ran, is recorded in the state file and returned.
    """
    state = read_state(paths)
    if not state.get("trusted"):
        raise ReviewError(f"the head {state['sha'][:7]} of {state['repo']}#{paths.number} is untrusted "
                          f"({state.get('untrusted_because') or 'no reason recorded'}), so its code does not "
                          "run; review it by reading")
    if not command:
        raise ReviewError("review.gate is not set in .projector.toml, so there is nothing to run; run the "
                          "repository's checks as review-pr describes")
    worktree = Path(state["worktree"])
    environ = dict(os.environ, PROJECTOR_WORKTREE=str(worktree), PROJECTOR_BASE=state["base"])
    ran_at = now()
    result = subprocess.run(["sh", "-c", command, "sh", str(worktree), state["base"]],
                            cwd=worktree, env=environ, stdout=2 if output_to_stderr else None)
    record = {"command": command, "exit_code": result.returncode, "sha": state["sha"],
              "ran_at": ran_at.isoformat()}
    state["gate"] = record
    write_state(paths, state)
    return record


# The pull request

def read_pull_request(repo: str, number: int) -> dict:
    """The pull request as GitHub's REST API gives it, open or not, or a refusal when it cannot be read."""
    try:
        return gh_json(["api", f"repos/{repo}/pulls/{number}"])
    except NotFound as exc:
        raise ReviewError(f"{repo}#{number} does not exist or is not visible to this account") from exc


def pull_request(repo: str, number: int) -> dict:
    """The open pull request, or a refusal saying why it cannot be reviewed."""
    pr = read_pull_request(repo, number)
    if pr.get("state") != "open":
        raise ReviewError(f"{repo}#{number} is {pr.get('state') or 'not open'}; only an open pull request is reviewed")
    return pr


def fetch(checkout: Path, ref: str) -> str:
    """Fetch `ref` from origin and return the commit it names, read from a ref this call alone writes and
    then deletes. `FETCH_HEAD` is shared by every fetch in the checkout, so another review's fetch, or a
    summary publish's, can rewrite it between this fetch and the read. The fetch leaves `FETCH_HEAD` to
    the operator's own fetches."""
    local = f"refs/projector/review-fetch/{os.getpid()}-{secrets.token_hex(4)}"
    git("-C", str(checkout), "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "origin", f"+{ref}:{local}")
    try:
        return git("-C", str(checkout), "rev-parse", "--verify", f"{local}^{{commit}}")
    finally:
        with suppress(ReviewError):
            git("-C", str(checkout), "update-ref", "-d", local)


def fetch_head(checkout: Path, repo: str, pr: dict) -> str:
    """Fetch the head — its branch in this repository, or `pull/<n>/head` from a fork — and
    the base into the checkout, verify the head, and return the merge base."""
    sha = pr["head"]["sha"]
    same = ((pr["head"].get("repo") or {}).get("full_name")) == repo
    ref = f"refs/heads/{pr['head']['ref']}" if same else f"refs/pull/{pr['number']}/head"
    fetched = fetch(checkout, ref)
    if fetched != sha:
        raise ReviewError(f"the head moved to {fetched[:7]} while it was fetched; run the command again")
    fetch(checkout, f"refs/heads/{pr['base']['ref']}")
    return git("-C", str(checkout), "merge-base", sha, pr["base"]["sha"])


def add_worktree(checkout: Path, path: Path, sha: str) -> None:
    if path.exists():
        git("-C", str(checkout), "worktree", "remove", "--force", str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    git("-C", str(checkout), "worktree", "add", "--quiet", "--detach", str(path), sha)
    head = git("-C", str(path), "rev-parse", "HEAD")
    if head != sha:
        raise ReviewError(f"the scratch worktree {path} is at {head[:7]}, not {sha[:7]}; run the command again, "
                          "which replaces the worktree")


# Start comments

def live_effort() -> str:
    """The reasoning effort the host reports for this session, or "" when it reports none.

    Claude Code sets CLAUDE_EFFORT for the Bash tool and keeps it current through
    a mid-session /effort change; a host that reports nothing, such as Codex or a
    model without effort support, leaves the label out rather than have it guessed.
    """
    return os.environ.get("CLAUDE_EFFORT", "").strip()


def producer_segments(version: str, model: str, effort: str) -> str:
    """What produced the review, as its start comment and signature line both name it."""
    return f"`{version}` · model `{model}` · " + (f"effort `{effort}` · " if effort else "")


def start_comment(model: str, sha: str, moved_from: Optional[str] = None) -> str:
    lines = [f"{MARK} **Projector review started** · "
             f"{producer_segments(distribution_version(), model, live_effort())}reviewing `{sha[:7]}`", ""]
    if moved_from:
        lines += [f"The head moved from `{moved_from[:7]}` to `{sha[:7]}`; this review is being updated "
                  "for the new changes.", ""]
    lines.append(f"<!-- projector-start v=1 sha={sha} model={model} -->")
    return "\n".join(lines) + "\n"


START_MARKER = re.compile(r"^<!-- projector-start v=1 sha=([0-9a-f]{40})(?: model=(\S+))? -->$", re.M)
# The model a start comment's first line names, for a comment whose marker names none.
START_MODEL = re.compile(r"· model `([^`\n]+)` ·")


def start_comments(repo: str, number: int, reviewer: str) -> list[dict]:
    """The reviewer's start comments on the pull request, from every page, each with its `id`, `url`, the
    `sha` and `model` it names, and when it was created and last edited."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/issues/{number}/comments",
                   "--jq", '.[] | select(.body // "" | contains("projector-start")) '
                           '| {id, url: .html_url, login: .user.login, body, created_at, updated_at} '
                           '| @json']).splitlines()
    comments = []
    for row in filter(str.strip, rows):
        comment = json.loads(row)
        marker = START_MARKER.search(comment.get("body") or "")
        named = START_MODEL.search(comment.get("body") or "")
        model = (marker and marker.group(2)) or (named and named.group(1))
        if (comment.get("login") or "").lower() != reviewer.lower() or not marker or not model:
            continue
        comments.append({"id": comment["id"], "url": comment.get("url"), "sha": marker.group(1), "model": model,
                         "created_at": comment["created_at"],
                         "updated_at": comment.get("updated_at") or comment["created_at"]})
    return comments


def is_live(comment: dict, reviews: list[dict], at: datetime) -> bool:
    """Whether a start comment still means a review in progress at `at`: not once its model has a verdict
    among `reviews` on the comment's head submitted since the comment was created, nor once a day has
    passed since it was last edited, the age at which a lock is stale."""
    if at - parse_time(comment["updated_at"]) > STALE_AFTER:
        return False
    created, model = parse_time(comment["created_at"]), identity(comment["model"])
    for review in reviews:
        marker = verdict_marker(review.get("body") or "")
        if (marker is not None and marker.sha == comment["sha"] and identity(marker.model) == model
                and review.get("at") and parse_time(review["at"]) >= created):
            return False
    return True


# Holds

def holds(repo: str, number: int, author: str) -> list[dict]:
    """The hold comments on the pull request, from every page, each with its `id` and `url`. Only the
    pull request's `author` holds it: anyone can comment, so another account's comment carrying the
    marker holds nothing."""
    if not author:
        return []
    rows = run_gh(["api", "--paginate", f"repos/{repo}/issues/{number}/comments",
                   "--jq", f'.[] | select(.body // "" | contains("{HOLD_MARKER}")) '
                           '| {id, url: .html_url, login: .user.login} | @json']).splitlines()
    return [{"id": comment["id"], "url": comment.get("url")} for comment in map(json.loads, filter(str.strip, rows))
            if (comment.get("login") or "").lower() == author.lower()]


def hold_body(changes: list[str]) -> str:
    """A hold comment listing `changes`, with the marker on its last line."""
    bullets = "\n".join(f"- {change}" for change in changes)
    return f"Held in draft while the following changes are implemented:\n\n{bullets}\n\n{HOLD_MARKER}\n"


def read_hold(repo: str, comment: int) -> dict:
    """A hold comment as GitHub has it now: its `id` and `url`, the `changes` it lists, and whether the marker
    is its last line, as `marked`."""
    found = gh_json(["api", f"repos/{repo}/issues/comments/{comment}"]) or {}
    lines = [line.strip() for line in (found.get("body") or "").splitlines() if line.strip()]
    return {"id": comment, "url": found.get("html_url"),
            "changes": [m.group(1).strip() for m in map(HOLD_BULLET.match, lines) if m],
            "marked": bool(lines) and lines[-1] == HOLD_MARKER}


def hold_listing(comments: list[dict]) -> list[str]:
    """The changes a hold's comments list, in order, each once."""
    return list(dict.fromkeys(change for comment in comments for change in comment["changes"]))


def named_repo(repo: Optional[str], cwd: Path, purpose: str) -> str:
    """The repository `review hold` and `review finish` act on: `repo`, else the origin of the checkout `cwd`
    is in. Unlike the commands that follow a review, they never take one from review state: they delete
    comments, so they act only on a repository they were given or are run in. `purpose` completes the
    refusal outside a checkout, as "to hold a pull request in"."""
    if repo:
        return repo
    try:
        return origin_repo(checkout_root(cwd))
    except ReviewError as exc:
        raise ReviewError(f"{cwd} is not in a checkout whose origin is on GitHub, so there is no repository "
                          f"{purpose}; pass --repo owner/name") from exc


def hold_changes(texts: list[str], flag: str) -> list[str]:
    """The changes given to --add or --drop, trimmed and without repeats, each refused unless it is one line."""
    changes: list[str] = []
    for text in texts:
        change = text.strip()
        if not change or "\n" in change or "\r" in change:
            raise ReviewError(f"{flag} {text!r} is not a change: give each change as one line of text")
        if change not in changes:
            changes.append(change)
    return changes


def hold(repo: str, number: int, add: list[str], drop: list[Optional[str]]) -> dict:
    """`review hold`: post or extend the author's hold on the pull request with `add`, remove the changes
    `drop` names from it, or end it with a `drop` that names none (a bare --drop, given as None), as
    fix-pr/SKILL.md § Hold the pull request while changes are built describes. Only the author's comment
    holds a pull request, so every mode refuses anyone else, and no mode marks the pull request ready."""
    if bool(add) == bool(drop):
        raise ReviewError("pass --add or --drop")
    end = None in drop
    if end and len(drop) > 1:
        raise ReviewError("a bare --drop ends the whole hold, so it cannot be combined with --drop TEXT; run "
                          "--drop TEXT to remove a change, or --drop alone to end the hold")
    changes = hold_changes(add, "--add") if add else [] if end else hold_changes(drop, "--drop")
    pr = read_pull_request(repo, number)
    author = (pr.get("user") or {}).get("login") or ""
    login = authenticated_login()
    if login.lower() != author.lower():
        raise ReviewError(f"gh is signed in as {login}, and {repo}#{number} is {author or 'nobody'}'s; only the "
                          "pull request's author can hold it, because nothing reads another account's hold comment")
    held = holds(repo, number, author)
    if add:
        return hold_add(repo, pr, held, changes)
    if end:
        return hold_end(repo, pr, held)
    return hold_drop(repo, pr, held, changes)


def hold_result(repo: str, pr: dict, action: str, comment: Optional[dict], changes: list[str], draft: bool) -> dict:
    return {"repo": repo, "number": pr["number"], "head": pr["head"]["sha"], "action": action,
            "comment": {"id": comment["id"], "url": comment["url"]} if comment else None, "changes": changes,
            "draft": draft}


def write_hold(repo: str, number: int, comment: Optional[int], changes: list[str]) -> dict:
    """Post the hold comment, or edit `comment`, then read it back, refusing unless it lists `changes` and
    ends with the marker."""
    body = hold_body(changes)
    if comment is None:
        comment = gh_json(["api", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"])["id"]
    else:
        run_gh(["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{comment}", "-f", f"body={body}"])
    written = read_hold(repo, comment)
    if not written["marked"] or written["changes"] != changes:
        raise ReviewError(f"hold comment {comment} does not read back as written, listing the changes with "
                          f"{HOLD_MARKER} on its last line; check it at {written['url'] or 'the pull request'} "
                          "and run the command again")
    return written


def hold_add(repo: str, pr: dict, held: list[dict], changes: list[str]) -> dict:
    """Post the hold, or add `changes` to the newest one. Only once the comment reads back is the pull request
    drafted: to the review loop, a draft on an unchanged head with no hold open is an author who answered
    without pushing."""
    number = pr["number"]
    if pr.get("state") != "open":
        raise ReviewError(f"{repo}#{number} is {pr.get('state') or 'not open'}; only an open pull request is held")
    listing, newest = changes, None
    if held:
        newest = read_hold(repo, held[-1]["id"])
        listing = newest["changes"] + [change for change in changes if change not in newest["changes"]]
    written = write_hold(repo, number, newest["id"] if newest else None, listing)
    # Read again now that the hold is open, so a review that marked it ready meanwhile is drafted too.
    draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
    if not draft:
        run_gh(["pr", "ready", str(number), "--repo", repo, "--undo"])
        draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
        if not draft:
            raise ReviewError(f"{repo}#{number} is still ready after `gh pr ready --undo`, with hold comment "
                              f"{written['id']} open; run `gh pr ready {number} --repo {repo} --undo` and check it")
    return hold_result(repo, pr, "added" if newest else "posted", written, listing, draft)


def hold_drop(repo: str, pr: dict, held: list[dict], changes: list[str]) -> dict:
    """Remove `changes`, which the owner decided will not be built, from every hold comment of the author's
    that lists them, and delete a comment left with none. The hold ends only once no comment is left, on a
    head no push changed, which asks the review loop to re-review it."""
    number = pr["number"]
    if not held:
        raise ReviewError(f"{repo}#{number} has no hold open, so there is no change to drop")
    found = [read_hold(repo, comment["id"]) for comment in held]
    listing = hold_listing(found)
    unknown = [f'"{change}"' for change in changes if change not in listing]
    if unknown:
        raise ReviewError(f"the hold on {repo}#{number} lists no change {listed(unknown)}; it lists "
                          + (listed([f'"{change}"' for change in listing]) or "none"))
    kept = []
    for comment in found:
        left = [change for change in comment["changes"] if change not in changes]
        if left == comment["changes"]:
            kept.append(comment)
        elif left:
            kept.append(write_hold(repo, number, comment["id"], left))
        else:
            delete_comment(repo, comment["id"])
    draft = bool(pr.get("draft"))
    if kept:
        return hold_result(repo, pr, "dropped", kept[-1], hold_listing(kept), draft)
    return hold_result(repo, pr, "ended", found[-1], listing, draft)


def hold_end(repo: str, pr: dict, held: list[dict]) -> dict:
    """Delete every hold comment of the author's, leaving the draft state to the review of the head. The
    caller says when: fix-pr ends the hold once the push that carries every listed change succeeds, and
    nothing here checks that it did."""
    draft = bool(pr.get("draft"))
    if not held:
        return hold_result(repo, pr, "none", None, [], draft)
    found = [read_hold(repo, comment["id"]) for comment in held]
    for comment in found:
        delete_comment(repo, comment["id"])
    return hold_result(repo, pr, "ended", found[-1], hold_listing(found), draft)


def hold_lines(result: dict) -> list[str]:
    """`review hold` without --json: what it did, the comment, the changes the hold lists, and the draft state."""
    where, head = f"{result['repo']}#{result['number']}", result["head"][:7]
    comment, changes, action = result["comment"], result["changes"], result["action"]
    state = "it is a draft" if result["draft"] else "it is not a draft"
    count = f"{len(changes)} change{'' if len(changes) == 1 else 's'}"
    if action == "none":
        return [f"no hold is open on {where}; {state}"]
    if action in ("posted", "added"):
        return [f"{action} hold comment {comment['id']} on {where}: {comment['url']}",
                f"held in draft while {count} {'is' if len(changes) == 1 else 'are'} implemented: "
                + "; ".join(changes)]
    if action == "dropped":
        return [f"still held by comment {comment['id']} on {where}: {comment['url']}",
                f"{count} still listed: " + "; ".join(changes) + f"; {state}"]
    return [f"ended the hold on {where}, deleting hold comment {comment['id']}, which listed "
            + ("; ".join(changes) if changes else "no change"),
            f"the review loop reviews {head} next, and a clean verdict marks it ready; {state}"]


# Resolving which review a command means

def valid_loop(loop: Optional[str]) -> Optional[str]:
    if loop is not None and not LOOP_ID.match(loop):
        raise ReviewError(f"--loop {loop!r} is not a usable id; use letters, digits, '.', '_' and '-'")
    return loop


def set_up_repos(root: Path, number: int) -> list[str]:
    """The repositories with a review of a pull request numbered `number` set up here."""
    states = (root / "prs").glob(f"*/*/{number}/*/state.json") if (root / "prs").is_dir() else []
    return sorted({f"{p.parents[3].name}/{p.parents[2].name}" for p in states})


def find_repo(root: Path, number: int, repo: Optional[str], cwd: Path) -> str:
    """The repository a command after setup means: --repo, else the one with a review of this pull
    request set up here, else the origin of the checkout the command runs in."""
    if repo:
        return repo
    found = set_up_repos(root, number)
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ReviewError(f"reviews of #{number} are set up in {', '.join(found)}; pass --repo to pick one")
    try:
        return origin_repo(checkout_root(cwd))
    except ReviewError as exc:
        raise ReviewError(f"no review of #{number} is set up here; pass --repo owner/name") from exc


def find_model(root: Path, repo: str, number: int, model: Optional[str]) -> Optional[str]:
    """The model a command after setup means: --model, else the one model with a review of this pull
    request set up here, or None when none is."""
    if model is not None:
        model_key(model)
        return model
    folder = root / "prs" / repo / str(number)
    states = sorted(folder.glob("*/state.json")) if folder.is_dir() else []
    models = [json.loads(p.read_text(encoding="utf-8")).get("model") or p.parent.name for p in states]
    if len(models) > 1:
        raise ReviewError(f"reviews of {repo}#{number} by {', '.join(models)} are set up here; pass --model to "
                          "pick one")
    return models[0] if models else None


def review_paths(root: Path, repo: str, number: int, model: Optional[str]) -> Paths:
    """The paths of the review a command after setup means, refused when no review is set up here."""
    found = find_model(root, repo, number, model)
    if found is None:
        raise ReviewError(f"no review of {repo}#{number} is set up here; run "
                          f"`project review setup {number} --model <model-id>`")
    return Paths(root, repo, number, found)


# Commands

def setup(root: Path, number: int, checkout: Path, repo: Optional[str], reviewer: Optional[str],
          configured_reviewer: Optional[str], model: str, loop: Optional[str],
          rereview: bool, replace: Optional[int] = None) -> dict:
    checkout = checkout_root(checkout)
    origin = origin_repo(checkout)
    if repo and repo.lower() != origin.lower():
        raise ReviewError(f"--repo {repo} is not this checkout's origin {origin}; run from a checkout of {repo}")
    repo = origin
    paths = Paths(root, repo, number, model)
    earlier = stored_state(paths) or {}
    recorded = (earlier.get("start_comment") or {}).get("id")
    pr = pull_request(repo, number)
    sha = pr["head"]["sha"]
    lock = take_lock(paths, sha, loop)
    try:
        operator = authenticated_login()
        reviewer = reviewer_login(reviewer, configured_reviewer, operator)
        starts = start_comments(repo, number, reviewer)
        if replace is not None:
            check_replaceable(starts, replace, model, reviewer, repo, number)
        check_duplicate([comment for comment in starts if comment["id"] != replace],
                        reviewer_reviews(repo, number, reviewer), model, recorded, number)
        merge_base = fetch_head(checkout, repo, pr)
        worktree = paths.worktree(sha)
        add_worktree(checkout, worktree, sha)
        trusted, why = trusted_head(repo, pr, operator)
        token = reviewer_token(reviewer)
        posted = gh_json(["api", f"repos/{repo}/issues/{number}/comments",
                          "-f", f"body={start_comment(model, sha)}"], token)
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    for comment in starts:
        # The review this machine set up before and never published, and the stopped session's that
        # --replace names, give way to this one.
        if comment["id"] in (recorded, replace):
            delete_comment(repo, comment["id"], token)
    ensure_loop(root, loop)
    state = {
        "repo": repo,
        "number": number,
        "sha": sha,
        "base": merge_base,
        "base_ref": pr["base"]["ref"],
        "cross_repository": ((pr["head"].get("repo") or {}).get("full_name")) != repo,
        "trusted": trusted,
        "untrusted_because": why or None,
        "checkout": str(checkout),
        "worktree": str(worktree),
        # A loop keeps each head's worktree until the pull request closes, so `finish` removes them all.
        "worktrees": list(dict.fromkeys([*earlier.get("worktrees", []), str(worktree)])),
        "operator": operator,
        "reviewer": reviewer,
        "model": model,
        "identity": identity(model),
        "loop": loop,
        "rereview": rereview,
        "start_comment": {"id": posted["id"], "created_at": posted["created_at"]},
        "heads": 1,
    }
    write_state(paths, state)
    return state


def check_replaceable(starts: list[dict], comment: int, model: str, reviewer: str, repo: str, number: int) -> None:
    """Refuse a --replace that names anything but one of this model's start comments on the pull request."""
    found = next((start for start in starts if start["id"] == comment), None)
    if found is None:
        raise ReviewError(f"comment {comment} is not a start comment {reviewer} posted on {repo}#{number}, so "
                          "--replace deletes nothing; pass the id that setup named when it refused")
    if identity(found["model"]) != identity(model):
        raise ReviewError(f"start comment {comment} is model {found['model']}'s, and --replace replaces only model "
                          f"{model}'s; another model's review is a second opinion that never stops this setup")


def check_duplicate(starts: list[dict], reviews: list[dict], model: str, recorded: Optional[int],
                    number: int) -> None:
    """Refuse a review while a live start comment shows the same model reviewing the pull request in
    another session: one this machine's state for the model does not record."""
    at = now()
    for comment in starts:
        if identity(comment["model"]) == identity(model) and comment["id"] != recorded \
                and is_live(comment, reviews, at):
            raise ReviewError(
                f"model {comment['model']} is already reviewing this pull request in another session: start comment "
                f"{comment['id']} ({comment.get('url') or 'no URL'}) names {comment['sha'][:7]}, posted "
                f"{comment['created_at']}; if that session has stopped, run `project review setup {number} "
                f"--model {model} --replace {comment['id']}` with the other flags you passed, which deletes that "
                "comment and sets this review up in its place")


def delete_comment(repo: str, comment: int, token: Optional[str] = None) -> None:
    try:
        run_gh(["api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment}"], token)
    except NotFound:
        pass


def move(paths: Paths, loop: Optional[str]) -> dict:
    number = paths.number
    state = read_state(paths)
    checkout = Path(state["checkout"])
    pr = pull_request(state["repo"], number)
    sha, old = pr["head"]["sha"], state["sha"]
    if sha == old:
        raise ReviewError(f"the head of {state['repo']}#{number} is still {old[:7]}; there is nothing to move")
    lock = take_lock(paths, sha, loop if loop is not None else state.get("loop"))
    try:
        merge_base = fetch_head(checkout, state["repo"], pr)
        worktree = paths.worktree(sha)
        add_worktree(checkout, worktree, sha)
        trusted, why = trusted_head(state["repo"], pr, state["operator"])
        token = reviewer_token(state["reviewer"])
        comment = state["start_comment"]["id"]
        body = start_comment(state["model"], sha, moved_from=old)
        run_gh(["api", "-X", "PATCH", f"repos/{state['repo']}/issues/comments/{comment}",
                "-f", f"body={body}"], token)
        reread = gh_json(["api", f"repos/{state['repo']}/issues/comments/{comment}"])
        if f"sha={sha}" not in (reread or {}).get("body", ""):
            raise ReviewError(f"the start comment {comment} still does not name {sha[:7]} after the edit; run "
                              f"`project review move {number}` again, which records the move only once the edit "
                              "reads back")
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    paths.lock(old).unlink(missing_ok=True)
    state.update(sha=sha, base=merge_base, base_ref=pr["base"]["ref"], worktree=str(worktree),
                 trusted=trusted, untrusted_because=why or None,
                 cross_repository=((pr["head"].get("repo") or {}).get("full_name")) != state["repo"],
                 heads=state["heads"] + 1, worktrees=state["worktrees"] + [str(worktree)])
    write_state(paths, state)
    return state


def finish(root: Path, number: int, repo: Optional[str], model: Optional[str], force: bool, cwd: Path) -> dict:
    """`review finish`: end one model's review of the pull request on this machine. As the reviewer, delete
    the review's start comment and every other start comment of the model's that `is_live` no longer
    counts; remove every scratch worktree the state lists; clear the locks; and delete the state, keeping
    the loop's record. Refuses while the review is unpublished unless `force`, and succeeds when there is
    nothing to clean, so a second run changes nothing.

    The repository is `repo`, else the origin of the checkout `cwd` is in, never one taken from review state,
    so a run in one repository cannot end another's review of a pull request with the same number. Every
    refusal comes before anything is deleted."""
    named = repo
    repo = named_repo(repo, cwd, "to finish a review in")
    model = find_model(root, repo, number, model)
    result: dict = {"repo": repo, "number": number, "model": model, "comments": [], "worktrees": [],
                    "released": [], "state": False}
    paths = Paths(root, repo, number, model) if model else None
    state = stored_state(paths) if paths else None
    elsewhere = [found for found in set_up_repos(root, number) if found.lower() != repo.lower()]
    if state is None and named is None and elsewhere:
        raise ReviewError(f"no review of {repo}#{number}, this checkout's origin, is set up here, and a review of "
                          f"#{number} in {listed(elsewhere)} is; pass --repo to name the review to finish")
    if paths is None:
        return result
    if state is not None:
        if (state.get("repo") or "").lower() != repo.lower():
            raise ReviewError(f"the review state in {paths.folder} names {state.get('repo')}, not {repo}, so "
                              "finish leaves it alone")
        recorded = (state.get("start_comment") or {}).get("id")
        if recorded and not force:
            raise ReviewError(unfinished(state, recorded))
        result["comments"] = finished_starts(state, recorded)
        if result["comments"]:
            token = reviewer_token(state["reviewer"])
            for comment in result["comments"]:
                delete_comment(repo, comment, token)
        result["worktrees"] = remove_worktrees(paths, state)
    result["released"] = [str(lock) for lock in release(paths)]
    paths.request.unlink(missing_ok=True)
    if state is not None:
        paths.state.unlink(missing_ok=True)
        result["state"] = True
    with suppress(OSError):
        paths.folder.rmdir()
    return result


def unfinished(state: dict, recorded: int) -> str:
    """Why `finish` refuses a review whose state still records its start comment: a review set up and not
    published, or a publish that posted the review and stopped before it deleted the comment."""
    repo, number, model, sha = state["repo"], state["number"], state["model"], state["sha"][:7]
    discard = f"`project review finish {number} --repo {repo} --force`"
    published = state.get("published") or {}
    if published.get("sha") == state["sha"]:
        return (f"model {model}'s publish of its review of {repo}#{number} at {sha} did not finish: review "
                f"{published['id']} is posted, and start comment {recorded} still stands; run the same "
                f"`project review publish {number}` command again to finish it, or skip the rest with {discard}")
    return (f"model {model}'s review of {repo}#{number} at {sha} is unpublished, and its start comment "
            f"{recorded} still says it is in progress; publish it first, or discard it with {discard}, as when "
            "the pull request closed")


def finished_starts(state: dict, recorded: Optional[int]) -> list[int]:
    """The start comments `finish` deletes: the review's own, and every other of its model's that no longer
    means a review in progress. A pull request GitHub no longer shows has none."""
    repo, number, reviewer = state["repo"], state["number"], state["reviewer"]
    try:
        starts = [comment for comment in start_comments(repo, number, reviewer)
                  if identity(comment["model"]) == identity(state["model"])]
        reviews = reviewer_reviews(repo, number, reviewer) if starts else []
    except NotFound:
        return []
    at = now()
    return [comment["id"] for comment in starts if comment["id"] == recorded or not is_live(comment, reviews, at)]


def remove_worktrees(paths: Paths, state: dict) -> list[str]:
    """Remove every scratch worktree the state lists, then prune the checkout's record of them, and return
    those removed. When the checkout that made one is gone or no longer lists it, a worktree in the review's
    own worktree folder is deleted outright; Git refuses to remove any other path."""
    checkout = state["checkout"]
    removed = []
    for path in map(Path, dict.fromkeys(state["worktrees"])):
        if not path.exists():
            continue
        try:
            git("-C", checkout, "worktree", "remove", "--force", str(path))
        except ReviewError:
            if not path.is_relative_to(paths.worktrees):
                raise
            shutil.rmtree(path)
        removed.append(str(path))
    with suppress(ReviewError):
        git("-C", checkout, "worktree", "prune")
    with suppress(OSError):
        paths.worktrees.rmdir()
    return removed


def finish_lines(result: dict) -> list[str]:
    """`review finish` without --json: what it deleted, removed, and released."""
    where = f"{result['repo'] or ''}#{result['number']}" + (f" by model {result['model']}" if result["model"] else "")
    if not (result["state"] or result["comments"] or result["worktrees"] or result["released"]):
        return [f"no review of {where} is set up here; there is nothing to finish"]
    count = len(result["released"])
    return ([f"finished the review of {where}"]
            + [f"deleted start comment {comment}" for comment in result["comments"]]
            + [f"removed worktree {path}" for path in result["worktrees"]]
            + ([f"released {count} lock{'' if count == 1 else 's'}"] if count else [])
            + (["deleted its state; the loop's record of published reviews stays"] if result["state"] else []))


THREADS_QUERY = """query($o:String!,$r:String!,$n:Int!,$after:String){ repository(owner:$o,name:$r){
  pullRequest(number:$n){ reviewThreads(first:100, after:$after){
    pageInfo{ hasNextPage endCursor }
    nodes{ id isResolved isOutdated path line originalLine
      comments(first:50){ nodes{ databaseId body } } } } } } }"""


def census(repo: str, number: int) -> dict:
    """Every Projector finding thread on the pull request, read to the last page, with its
    replies, so the review can verify and settle each one from this one list."""
    owner, name = repo.split("/", 1)
    threads, after = [], None
    while True:
        args = ["api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"o={owner}", "-f", f"r={name}",
                "-F", f"n={number}"]
        if after:
            args += ["-f", f"after={after}"]
        page = gh_json(args)
        pr = ((page or {}).get("data") or {}).get("repository", {}).get("pullRequest")
        if pr is None:
            raise ReviewError(f"{repo}#{number} does not exist or is not visible to this account")
        connection = pr["reviewThreads"]
        for node in connection["nodes"]:
            comments = (node.get("comments") or {}).get("nodes") or []
            if comments and FINDING_MARKER in (comments[0].get("body") or ""):
                threads.append({
                    "id": node["id"],
                    "path": node.get("path"),
                    "line": node.get("line") if node.get("line") is not None else node.get("originalLine"),
                    "outdated": bool(node.get("isOutdated")),
                    "resolved": bool(node.get("isResolved")),
                    "comments": [{"id": c.get("databaseId"), "body": c.get("body") or ""} for c in comments],
                })
        if not connection["pageInfo"]["hasNextPage"]:
            break
        after = connection["pageInfo"]["endCursor"]
    resolved = sum(1 for t in threads if t["resolved"])
    return {"total": len(threads), "resolved": resolved, "open": len(threads) - resolved, "threads": threads}


def census_lines(result: dict) -> list[str]:
    noun = "thread" if result["total"] == 1 else "threads"
    lines = [f"{result['total']} finding {noun}: {result['resolved']} resolved, {result['open']} open"]
    for t in result["threads"]:
        if not t["resolved"]:
            lines.append(f"{t['path']}:{t['line']}{' outdated' if t['outdated'] else ''}")
    return lines


# Settling a finding thread

# What each result does to the thread, as review-pr/SKILL.md § Verify earlier findings describes: resolve it,
# unresolve it, or leave it as it is.
SETTLED = {"fixed": True, "accepted": True, "withdrawn": True, "kept": None, "reopened": False}
# A Projector marker at the start of a line, which a reply's body leaves to `settle`.
ANY_MARKER = re.compile(r"^\s*<!--\s*projector-", re.M)
THREAD_QUERY = """query($t:ID!){ node(id:$t){ ... on PullRequestReviewThread { isResolved
  pullRequest{ number repository{ nameWithOwner } }
  first: comments(first:1){ nodes{ body } }
  last: comments(last:1){ nodes{ databaseId body } } } } }"""
REPLY_MUTATION = """mutation($t:ID!,$b:String!){
  addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t, body:$b}){ comment{ databaseId } } }"""
RESOLVE_MUTATION = """mutation($t:ID!){ resolveReviewThread(input:{threadId:$t}){ thread{ isResolved } } }"""
UNRESOLVE_MUTATION = """mutation($t:ID!){ unresolveReviewThread(input:{threadId:$t}){ thread{ isResolved } } }"""


def verify_marker(result: str, sha: str, model: str) -> str:
    """The line that opens a reviewer's reply on a finding thread, so neither loop reads the reply as a new
    finding or as the author answering."""
    return f"<!-- projector-verify v=1 result={result} sha={sha} model={model} -->"


def finding_thread(repo: str, number: int, thread: str) -> dict:
    """The Projector finding thread `thread` on the pull request as GitHub has it now: whether it is
    `resolved`, and its `last` comment. Refuses any other thread, comment, or id."""
    wrong = (f"{thread} is not a review thread on {repo}#{number}; pass a thread's id from "
             f"`project review census {number} --json`")
    try:
        found = gh_json(["api", "graphql", "-f", f"query={THREAD_QUERY}", "-f", f"t={thread}"])
    except NotFound as exc:
        raise ReviewError(wrong) from exc
    node = ((found or {}).get("data") or {}).get("node") or {}
    pr = node.get("pullRequest") or {}
    if pr.get("number") != number or ((pr.get("repository") or {}).get("nameWithOwner") or "").lower() != repo.lower():
        raise ReviewError(wrong)
    first = (node["first"]["nodes"] or [{}])[0]
    if FINDING_MARKER not in (first.get("body") or ""):
        raise ReviewError(f"thread {thread} on {repo}#{number} carries no Projector finding; another reviewer's "
                          "thread is theirs to settle")
    return {"resolved": bool(node.get("isResolved")), "last": (node["last"]["nodes"] or [{}])[-1]}


def reply_text(body_path: Path) -> str:
    if not body_path.is_file():
        raise ReviewError(f"the body file {body_path} does not exist; write the reply there first")
    text = body_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ReviewError("the body is empty; say what at this head settles the finding, or what still fails")
    if ANY_MARKER.search(text):
        raise ReviewError("leave the marker out of the body; settle writes it from the review's state")
    return text


def settle(paths: Paths, thread: str, result: str, body_path: Path) -> dict:
    """`review settle`: as the reviewer, reply on a finding thread with the verify marker filled from the
    review's state, resolve or unresolve the thread as `result` asks, and read both back. A reply already
    carrying the same marker as the thread's last comment is not posted again, so a run that stopped after
    replying finishes when it runs again."""
    if result not in SETTLED:
        raise ReviewError(f"--result must be one of {', '.join(SETTLED)}, not {result}")
    state = read_state(paths)
    repo, number = state["repo"], state["number"]
    text = reply_text(body_path)
    marker = verify_marker(result, state["sha"], state["model"])
    found = finding_thread(repo, number, thread)
    token = reviewer_token(state["reviewer"])
    if (found["last"].get("body") or "").startswith(marker):
        comment = found["last"]["databaseId"]
    else:
        posted = gh_json(["api", "graphql", "-f", f"query={REPLY_MUTATION}", "-f", f"t={thread}",
                          "-f", f"b={marker}\n{text}\n"], token)
        comment = posted["data"]["addPullRequestReviewThreadReply"]["comment"]["databaseId"]
    wanted = SETTLED[result]
    if wanted is not None:
        run_gh(["api", "graphql", "-f", f"query={RESOLVE_MUTATION if wanted else UNRESOLVE_MUTATION}",
                "-f", f"t={thread}"], token)
    reply = gh_json(["api", f"repos/{repo}/pulls/comments/{comment}"]) or {}
    if not (reply.get("body") or "").startswith(marker):
        raise ReviewError(f"reply {comment} on thread {thread} does not open with {marker} when read back; check it "
                          f"at {reply.get('html_url') or 'the pull request'} and run settle again")
    resolved = finding_thread(repo, number, thread)["resolved"]
    if wanted is not None and resolved != wanted:
        raise ReviewError(f"thread {thread} is {'resolved' if resolved else 'open'} after {result}; run settle "
                          "again")
    return {"repo": repo, "number": number, "thread": thread, "result": result, "sha": state["sha"],
            "model": state["model"], "comment": comment, "url": reply.get("html_url"), "resolved": resolved}


def settle_lines(result: dict) -> list[str]:
    """`review settle` without --json: the reply it posted and where the thread stands."""
    stands = "resolved" if result["resolved"] else "open"
    return [f"replied {result['result']} on thread {result['thread']} of {result['repo']}#{result['number']} at "
            f"{result['sha'][:7]}: {result['url'] or 'comment ' + str(result['comment'])}",
            f"the thread is {stands}"]


# Incremental reviews

class Unmeasurable(ReviewError):
    pass


def git_run(checkout: Path, *args: str, stdin: bytes = b"", ok: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Run git in the checkout for its bytes and exit status; a status outside `ok` cannot be measured."""
    result = subprocess.run(["git", "-C", str(checkout), *args], input=stdin, capture_output=True)
    if result.returncode not in ok:
        raise Unmeasurable(f"git {' '.join(args[:3])} exited {result.returncode}: "
                           f"{result.stderr.decode('utf-8', 'replace').strip()}")
    return result


def decoded(output: bytes) -> str:
    return output.decode("utf-8", "replace")


def is_ancestor(checkout: Path, ancestor: str, descendant: str) -> bool:
    return git_run(checkout, "merge-base", "--is-ancestor", ancestor, descendant, ok=(0, 1)).returncode == 0


def merges_base(checkout: Path, old: str, new: str, new_base: str) -> bool:
    """Whether `new` brought in the base it now forks from through a merge commit since `old`."""
    if is_ancestor(checkout, new_base, old):
        return False
    merges = decoded(git_run(checkout, "rev-list", "--merges", f"{old}..{new}").stdout).split()
    return any(is_ancestor(checkout, new_base, merge) for merge in merges)


def patch_ids(checkout: Path, base: str, head: str) -> set[str]:
    """The stable patch id of each commit in base..head other than merges, as range-diff matches them."""
    commits = git_run(checkout, "rev-list", "--no-merges", f"{base}..{head}").stdout
    if not commits.strip():
        return set()
    patches = git_run(checkout, "diff-tree", "--stdin", "--root", "-r", "-p", "--no-renames", stdin=commits).stdout
    ids = git_run(checkout, "patch-id", "--stable", stdin=patches).stdout
    return {line.split()[0] for line in decoded(ids).splitlines() if line.strip()}


@dataclass
class Measurement:
    kind: str
    unchanged_commits: Optional[bool]
    conflicts: list[str]
    insertions: int
    deletions: int
    files: list[str]
    unreadable: list[str]
    patch: str


def measure(checkout: Path, old: str, old_base: str, new: str, new_base: str) -> Measurement:
    """What the pull request's diff against its base gained or lost from head `old` to head `new`.

    A diff of the two heads would also count what a merge or a rebase brought
    in from the base. So when the merge base moved, `old`'s change is replayed
    onto the new one with `merge-tree`, which needs no ancestry between the
    heads, and the result is diffed against `new`. A conflicting replay keeps
    diff3 markers in the file, so the diff shows how the author resolved it.
    `old_base` is the merge base recorded when `old` was reviewed, because
    after a stack's parent is rewritten, the merge base Git would compute
    brings the parent's old commits into the replay. `merge-tree` writes
    objects and touches neither the working tree nor the index.

    Raises Unmeasurable when the checkout lacks a commit or the replay cannot
    run, as on a Git older than 2.40, which has no `--merge-base`."""
    for sha, what in ((old, "the earlier head"), (old_base, "its recorded merge base"),
                      (new, "the head"), (new_base, "the head's merge base")):
        if git_run(checkout, "cat-file", "-e", f"{sha}^{{commit}}", ok=(0, 1, 128)).returncode:
            raise Unmeasurable(f"the checkout lacks {what} {sha[:7]}")
    moved = old_base != new_base
    conflicts: list[str] = []
    tree = old
    if moved:
        # With -z the output is the tree, each conflicted path, an empty field, and then messages.
        replay = git_run(checkout, "-c", "merge.conflictStyle=diff3", "merge-tree", "--write-tree", "--name-only",
                         "-z", f"--merge-base={old_base}", new_base, old, ok=(0, 1))
        fields = decoded(replay.stdout).split("\0")
        tree = fields[0].strip()
        if replay.returncode == 1:
            end = fields.index("", 1) if "" in fields[1:] else len(fields)
            conflicts = list(dict.fromkeys(fields[1:end]))

    def diff(*options: str) -> str:
        return decoded(git_run(checkout, "diff-tree", "-r", "--no-renames", *options, tree, new).stdout)

    numstat, raw = diff("--numstat", "-z").split("\0"), diff("--raw", "-z").split("\0")
    insertions = deletions = 0
    files, unreadable = [], []
    for row in numstat:
        if not row:
            continue
        added, removed, path = row.split("\t", 2)
        files.append(path)
        if added == "-":
            unreadable.append(path)
        else:
            insertions, deletions = insertions + int(added), deletions + int(removed)
    # --raw -z gives each file's ":<old mode> <new mode> <old id> <new id> <status>" and then its path.
    for status, path in zip(raw[0::2], raw[1::2]):
        modes = status.lstrip(":").split()[:2]
        if any(mode in ("120000", "160000") for mode in modes) and path not in unreadable:
            unreadable.append(path)
    patch = diff("-p")
    ancestor = is_ancestor(checkout, old, new)
    if ancestor:
        kind = "push" if not moved else "merge" if merges_base(checkout, old, new, new_base) else "retarget"
        unchanged = None
    else:
        kind = "rebase" if moved else "rewrite"
        unchanged = patch_ids(checkout, old_base, old) <= patch_ids(checkout, new_base, new)
    return Measurement(kind, unchanged, conflicts, insertions, deletions, files, unreadable, patch)


class Refused(Exception):
    pass


def gate_passed(state: dict) -> bool:
    record = state.get("gate") or {}
    return record.get("sha") == state["sha"] and record.get("exit_code") == 0


def producer(version: Optional[str], model: str, effort: Optional[str]) -> str:
    """What produced a review, for a refusal to name: a marker that names no version reads as `unknown`."""
    return f"Projector {version or 'unknown'}, model {model}, " + (f"effort {effort}" if effort else "no effort")


def incremental_rules(root: Path, state: dict, loop: Optional[str], enabled: bool,
                      reviews: Optional[list[dict]] = None) -> dict:
    """Whether the head may have an incremental review, with the change measured since this loop's
    last full review of the pull request.

    These are the facts a read of the change cannot see, and `review interdiff`
    and `publish --incremental` both apply them. They are checked in order, and
    the first that fails is the `reason`. The gate rule is last, so a refusal
    that names it means every other rule passed, and `needs_gate` says so: a
    moved merge base brings in code no review ran beside the pull request's own,
    so the head needs a passing `project review gate` of its own. `reviews` is
    the reviewer's listing from `reviewer_reviews`, read here when not given."""
    result: dict = {"incremental": False, "reason": None, "needs_gate": False, "from": None, "review": None,
                    "kind": None, "unchanged_commits": None, "conflicts": None, "insertions": None,
                    "deletions": None, "files": [], "patch": ""}
    repo, number, sha = state["repo"], state["number"], state["sha"]
    try:
        if not enabled:
            raise Refused("review.incremental is false")
        if loop is None:
            raise Refused("no review loop runs this review, so it has no full review of its own to build on")
        if not state.get("trusted"):
            raise Refused(f"the head {sha[:7]} is untrusted ({state.get('untrusted_because') or 'no reason recorded'})")
        entries = [r for r in read_record(root, loop) if r.get("repo") == repo and r.get("number") == number]
        if entries and entries[-1].get("verdict") != "clean":
            raise Refused(f"the newest verdict in loop {loop}'s record requests changes")
        if entries and entries[-1].get("sha") == sha:
            raise Refused(f"loop {loop} already published a verdict on {sha[:7]}")
        full = next((r for r in reversed(entries) if not r.get("from")), None)
        if full is None or full.get("verdict") != "clean":
            raise Refused(f"loop {loop}'s record holds no clean full review of {repo}#{number}")
        result["from"] = full["sha"]
        if reviews is None:
            reviews = reviewer_reviews(repo, number, state["reviewer"])
        earlier = next((r for r in reviews if r.get("id") == full.get("review_id")), None)
        if earlier is None:
            raise Refused(f"the full review {full.get('review_id')} of {full['sha'][:7]} is missing from GitHub")
        result["review"] = earlier.get("url")
        marker = verdict_marker(earlier.get("body") or "")
        mine = (distribution_version(), state["model"], live_effort() or None)
        if marker is None or marker.sha != full["sha"]:
            raise Refused(f"the full review of {full['sha'][:7]} carries no marker naming that head")
        if (marker.projector, marker.model, marker.effort) != mine:
            raise Refused(f"the full review of {full['sha'][:7]} was made by "
                          f"{producer(marker.projector, marker.model, marker.effort)}, and this run is "
                          f"{producer(*mine)}")
        open_findings = census(repo, number)["open"]
        if open_findings:
            raise Refused(f"{open_findings} finding thread{'' if open_findings == 1 else 's'} "
                          f"{'is' if open_findings == 1 else 'are'} open")
        if not full.get("base"):
            raise Refused(f"the change cannot be measured: loop {loop}'s record holds no merge base for the "
                          f"full review of {full['sha'][:7]}, as a record an earlier release wrote does not")
        try:
            measured = measure(Path(state["checkout"]), full["sha"], full["base"], sha, state["base"])
        except Unmeasurable as exc:
            raise Refused(f"the change cannot be measured: {exc}") from exc
        result.update(kind=measured.kind, unchanged_commits=measured.unchanged_commits,
                      conflicts=measured.conflicts, insertions=measured.insertions, deletions=measured.deletions,
                      files=measured.files, patch=measured.patch)
        if measured.unreadable:
            raise Refused("the change touches a binary file, a symlink, or a submodule: "
                          + ", ".join(measured.unreadable))
        if full["base"] != state["base"] and not gate_passed(state):
            result["needs_gate"] = True
            raise Refused("the merge base moved, and no `project review gate` run on this head has exited 0")
    except Refused as refusal:
        result["reason"] = str(refusal)
        return result
    result["incremental"] = True
    return result


def interdiff_lines(result: dict, base_ref: str) -> list[str]:
    """`review interdiff`'s output without --json: what it found, the verdict on the rules, and the patch."""
    lines = []
    if result["from"]:
        lines.append(f"earlier full review: {result['from'][:7]}" + (f", {result['review']}" if result["review"] else ""))
    if result["kind"]:
        kind = KINDS[result["kind"]].format(base=base_ref)
        if result["unchanged_commits"] is not None:
            kind += ("; every reviewed commit carried over unchanged" if result["unchanged_commits"]
                     else "; a reviewed commit changed")
        lines.append(f"kind: {kind}")
        if result["conflicts"]:
            lines.append(f"conflicts: {', '.join(result['conflicts'])}")
        lines.append(f"change: {change_size(result)}")
    if result["incremental"]:
        lines.append("incremental: every rule passes")
    else:
        lines.append(f"refused: {result['reason']}" + (" (needs_gate)" if result["needs_gate"] else ""))
    if result["patch"]:
        lines += ["", result["patch"].rstrip("\n")]
    return lines


def change_size(result: dict) -> str:
    count = len(result["files"])
    return f"+{result['insertions']} −{result['deletions']} lines in {count} file{'' if count == 1 else 's'}"


# Publishing

PLACEHOLDERS = ("took", "seconds", "sha", "short_sha", "census")
PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
# Code the body quotes is left exactly as written: fenced blocks, then inline spans.
CODE = re.compile(r"^(```|~~~).*?^\1[^\n]*$|`[^`\n]+`", re.S | re.M)
# An inline span that holds a placeholder and nothing else asks for the value in code font,
# as `{short_sha}` does, so it is filled; a placeholder inside longer code is quoted code.
LONE_PLACEHOLDER = re.compile(r"`" + PLACEHOLDER.pattern + r"`")
# A comment of Projector's own at the start of a line, as a review or finding carries it.
OWN_MARKER = re.compile(r"^\s*<!--\s*projector-(review|finding|incremental|suggestions)\b", re.M)
SIGNATURE = re.compile(
    r"^" + re.escape(MARK) + r" \*\*Projector review\*\* of `[0-9a-f]{7}` · `[^`\n]+` · model `[^`\n]+` · "
    r"(?:effort `[^`\n]+` · )?\*\*(CLEAN|CHANGES REQUESTED)\*\* · (?:incremental from `[0-9a-f]{7}` · )?"
    r"took (\d+m \d{2}s|\d+h \d{2}m)( over \d+ heads)?$"
)
# A review's marker line. Publish writes `projector=` and `clean`, but a marker an earlier release wrote
# may name no version or spell a clean verdict `approved`, and it still reads as the verdict it gives. Every
# earlier release reads a marker holding a field it does not know as no verdict, so a new fact about a review
# goes on a line of its own, as the incremental line and the suggestions count do.
MARKER = re.compile(
    r"^<!-- projector-review v=1 verdict=(clean|approved|changes-requested) (?:projector=(\S+) )?model=(\S+) "
    r"(?:effort=(\S+) )?sha=([0-9a-f]{40}) findings=\d+ seconds=\d+ covered=\d+/\d+ -->$"
)
# How a head moved since the full review, by kind: `interdiff` prints the phrase, and an incremental
# review's lead names every kind but a push as "across a <phrase>".
KINDS = {"push": "push", "merge": "merge of {base}", "retarget": "retarget onto {base}",
         "rebase": "rebase onto {base}", "rewrite": "rewrite of its commits"}
# The line after an incremental review's marker, naming the full review it builds on.
INCREMENTAL = re.compile(
    r"^<!-- projector-incremental v=1 from=([0-9a-f]{40}) kind=(" + "|".join(KINDS) + r") "
    r"insertions=\d+ deletions=\d+ files=\d+ conflicts=\d+ -->$"
)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
PRIORITY_HEADER = re.compile(r"^\*\*(P1|P2) · [^\n]+\*\*")
VERDICT_WORDS = {"clean": "CLEAN", "changes-requested": "CHANGES REQUESTED"}
INCREMENTAL_BODY_LIMIT = 600


@dataclass(frozen=True)
class Verdict:
    """What a review's marker says: the verdict, the head it judged, what produced it, and, for an
    incremental review, the head of the full review it builds on. `projector` is None for a marker
    that names no version."""

    verdict: str
    sha: str
    projector: Optional[str]
    model: str
    effort: Optional[str]
    incremental_from: Optional[str]


def verdict_marker(body: str) -> Optional[Verdict]:
    """The verdict a review body's marker line carries, or None when no line is a marker.

    The verdict is `clean` or `changes-requested`, so a marker that spells a
    clean verdict `approved` reads as `clean`. An incremental review's line
    counts only directly after the marker, where publish writes it."""
    lines = [line.strip() for line in body.splitlines()]
    for i, line in enumerate(lines):
        marker = MARKER.match(line)
        if marker is None:
            continue
        verdict, projector, model, effort, sha = marker.groups()
        incremental = INCREMENTAL.match(lines[i + 1]) if i + 1 < len(lines) else None
        return Verdict("clean" if verdict == "approved" else verdict, sha, projector, model, effort,
                       incremental.group(1) if incremental else None)
    return None


def duration(seconds: int, heads: int) -> str:
    """How long the review took, as the signature line writes it: minutes and seconds
    under an hour, hours and minutes past it, and how many heads the time covers."""
    if seconds < 3600:
        text = f"{seconds // 60}m {seconds % 60:02d}s"
    else:
        text = f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    return text + (f" over {heads} heads" if heads > 1 else "")


def elapsed(created_at: str, at: datetime) -> int:
    """Whole seconds from the start comment's `created_at` to `at`, both in UTC."""
    return max(0, int((at - parse_time(created_at)).total_seconds()))


def diff_lines(repo: str, number: int) -> dict[str, list[tuple[int, int]]]:
    """Each changed file's right-side hunk ranges, from every page of the pull request's files."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/files",
                   "--jq", '.[] | [.filename, (.patch // "")] | @json']).splitlines()
    ranges: dict[str, list[tuple[int, int]]] = {}
    for row in rows:
        if not row.strip():
            continue
        path, patch = json.loads(row)
        spans = ranges.setdefault(path, [])
        for start, count in HUNK.findall(patch):
            count = 1 if count == "" else int(count)
            if count:
                spans.append((int(start), int(start) + count - 1))
    return ranges


def load_threads(path: Optional[Path]) -> list[dict]:
    if path is None:
        return []
    if not path.is_file():
        raise ReviewError(f"the threads file {path} does not exist; write the findings there first")
    try:
        threads = json.loads(path.read_text(encoding="utf-8") or "[]")
    except ValueError as exc:
        raise ReviewError(f"the threads file {path} is not JSON: {exc}") from exc
    if not isinstance(threads, list):
        raise ReviewError(f"the threads file {path} must hold a list of findings")
    return threads


def is_suggestion(entry: object) -> bool:
    """Whether an entry of the threads file is a P3, which `publish` lists in the body rather than posting."""
    return isinstance(entry, dict) and entry.get("priority") == "P3"


def check_threads(threads: list[dict], ranges: dict[str, list[tuple[int, int]]]) -> None:
    """Refuse a P1 or P2 finding GitHub could not anchor or a reader could not act on. `suggestion_entries`
    checks the P3 entries."""
    for i, t in enumerate(threads, 1):
        where = f"finding {i}"
        if not isinstance(t, dict):
            raise ReviewError(f"{where} is not an object with path, line, priority, and body")
        if is_suggestion(t):
            continue
        path, line, priority, body = t.get("path"), t.get("line"), t.get("priority"), t.get("body") or ""
        if not path or not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ReviewError(f"{where} needs a path and a line number (got {path!r}:{line!r}); "
                              "anchor it to a line the diff changes")
        spans = ranges.get(path)
        if spans is None:
            raise ReviewError(f"{where}: {path} is not a file this pull request changes, or GitHub has no "
                              "patch for it; anchor the finding to a changed line")
        if not any(a <= line <= b for a, b in spans):
            listed = ", ".join(f"{a}-{b}" for a, b in spans) or "none"
            raise ReviewError(f"{where}: {path}:{line} is outside the pull request's diff hunks ({listed}); "
                              "anchor it to a changed line")
        if priority not in ("P1", "P2"):
            raise ReviewError(f"{where}: priority must be P1, P2, or P3")
        if OWN_MARKER.search(body):
            raise ReviewError(f"{where}: leave the finding marker out of the body; publish writes it with "
                              "the head's SHA")
        header = PRIORITY_HEADER.match(body)
        if not header or header.group(1) != priority:
            raise ReviewError(f"{where}: the body must open with `**{priority} · <what goes wrong>**`")
        if "**Fix:**" not in body:
            raise ReviewError(f"{where}: the body must carry a `**Fix:**` line")


# Suggestions

# The label of a review body's suggestions section, in every form a reviewer has written one: a heading of
# level 1 to 4, a line in bold, or a plain `Suggestions:` line, in any case, with an aside such as `(P3)`.
SUGGESTIONS_NAME = r"suggestions(?:\s*\([^)\n]*\))?"
SUGGESTIONS_LABEL = re.compile(rf"^(?:#{{1,4}}\s*{SUGGESTIONS_NAME}:?|\*\*{SUGGESTIONS_NAME}:?\*\*:?|"
                               rf"{SUGGESTIONS_NAME}:)$", re.I)
# A line that ends the section: another heading, a line in bold, or a `Label:` line such as `What was checked:`.
SECTION_END = re.compile(r"^(?:#{1,6}(?:\s|$)|\*\*[^*\n].*\*\*:?$|[A-Z][^:\n]*:$)")
# A list item's indent and text.
LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
FENCE = re.compile(r"^(```|~~~)")
# The file and line a suggestion opens with: in code font, as `publish` writes it, or bare as path:line.
CODE_PLACE = re.compile(r"`([^`\s:]+)(?::(\d+)(?:[-–]\d+)?)?`(\s*:)?")
BARE_PLACE = re.compile(r"([^`\s:]+):(\d+)(?:[-–]\d+)?(?=[\s:,;.]|$)")
# What joins that place to the suggestion's text.
SEPARATOR = re.compile(r"^\s*(?:[:,;.—–]|--?(?=\s))?\s*")
SUGGESTION_PATH = re.compile(r"[^\s`:]+")
# The line `publish` writes directly before a suggestions section's label, counting its items. It is a line of
# its own rather than a field of the review's marker, which every earlier release would then read as no verdict.
SUGGESTIONS_COUNT = re.compile(r"^<!-- projector-suggestions v=1 count=(\d+) -->$", re.M)


def suggestion_entries(threads: list) -> list[dict]:
    """The threads file's P3 entries, as the suggestions `publish` lists in the review body: each its `path`,
    its `line` or None, and its `body` joined onto one line as its `text`. Refuses an entry the list could not
    show as `suggestions_section` writes it."""
    entries = []
    for i, t in enumerate(threads, 1):
        if not is_suggestion(t):
            continue
        where, path, line, body = f"finding {i}", t.get("path"), t.get("line"), t.get("body")
        text = " ".join(part.strip() for part in body.splitlines() if part.strip()) if isinstance(body, str) else ""
        if not isinstance(path, str) or not SUGGESTION_PATH.fullmatch(path):
            raise ReviewError(f"{where} is a P3 and needs the path of the file it suggests changing, with no space, "
                              f"colon, or backtick (got {path!r})")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ReviewError(f"{where}: a P3's line is a line number, or left out (got {line!r})")
        if not text:
            raise ReviewError(f"{where}: a P3 needs a body that says the change")
        if re.search(r"<!--\s*projector-", text):
            raise ReviewError(f"{where}: leave Projector's markers out of a P3's body")
        entries.append({"path": path, "line": line, "text": text})
    return entries


def place(item: dict) -> str:
    """The file and line a suggestion names, as `path:line`, or `path` when it names no line."""
    return item["path"] + ("" if item["line"] is None else f":{item['line']}")


def suggestions_section(entries: list[dict]) -> str:
    """The suggestions section `publish` ends a review body with, in the one form `method.md` § 7 gives: a
    `### Suggestions` heading and an item per suggestion, opening with its `path:line` in code font."""
    return "### Suggestions\n\n" + "\n".join(f"- `{place(entry)}`: {entry['text']}" for entry in entries)


def suggestions_label(lines: list[str]) -> Optional[int]:
    """The index of the first line outside fenced code that `SUGGESTIONS_LABEL` matches, or None."""
    fenced = False
    for i, line in enumerate(lines):
        if FENCE.match(line.strip()):
            fenced = not fenced
        elif not fenced and SUGGESTIONS_LABEL.match(line.strip()):
            return i
    return None


def with_suggestions_count(body: str) -> str:
    """`body` with the line that counts its suggestions directly before their label, or as it is when it lists
    none."""
    items = parse_suggestions(body)
    if not items:
        return body
    lines = body.splitlines()
    lines.insert(suggestions_label(lines), f"<!-- projector-suggestions v=1 count={len(items)} -->")
    return "\n".join(lines)


def parse_suggestions(body: str) -> Optional[list[dict]]:
    """The items of a review body's suggestions section, each its `path` and `line`, None where the item names
    none, and its `text`, or None when the body has no such section.

    The section opens at the first line outside fenced code that `SUGGESTIONS_LABEL` matches, and ends at a
    line `SECTION_END` matches or at a paragraph after a blank line. Each list item at the first item's indent
    is a suggestion, and the lines under it, a deeper list included, join its text."""
    lines = body.splitlines()
    start = suggestions_label(lines)
    if start is None:
        return None
    items: list[list[str]] = []
    indent: Optional[int] = None
    blank = fenced = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        fence = bool(FENCE.match(stripped))
        fenced = fenced != fence
        item = LIST_ITEM.match(line)
        if fenced or fence:
            if items:
                items[-1].append(stripped)
        elif item and (indent is None or len(item.group(1)) <= indent):
            indent = len(item.group(1)) if indent is None else indent
            items.append([item.group(2).strip()])
            blank = False
        elif not stripped:
            blank = True
        elif SECTION_END.match(stripped) or blank and items and not line[0].isspace():
            break
        elif items:
            items[-1].append(stripped)
            blank = False
    return [suggestion_item(" ".join(parts)) for parts in items]


def suggestion_item(text: str) -> dict:
    """A suggestion's `path`, `line`, and `text`, from an item that opens with the place it names, or with
    neither when it opens with none. A code span opens with a place when it holds a line number, a `/` or a
    `.`, or a colon follows it."""
    code, bare = CODE_PLACE.match(text), BARE_PLACE.match(text)
    if code and (code.group(2) or code.group(3) or re.search(r"[/.]", code.group(1))):
        path, line, rest = code.group(1), code.group(2), text[code.end():]
    elif bare:
        path, line, rest = bare.group(1), bare.group(2), text[bare.end():]
    else:
        return {"path": None, "line": None, "text": text}
    return {"path": path, "line": int(line) if line else None, "text": SEPARATOR.sub("", rest)}


def listed_suggestions(review: dict) -> int:
    """How many suggestions a review lists: the count `publish` wrote before their label, or, in a review an
    earlier release published, the items of its body's section."""
    body = review.get("body") or ""
    found = SUGGESTIONS_COUNT.search(body)
    return int(found.group(1)) if found else len(parse_suggestions(body) or [])


def with_full_review(review: dict, reviews: list[dict]) -> list[dict]:
    """The reviews whose suggestions stand with `review`: `review` itself and, when it is incremental, the full
    review among `reviews` that its lead links, whose suggestions it leaves unrepeated."""
    body = review.get("body") or ""
    marker = verdict_marker(body)
    link = FULL_REVIEW_LINK.search(body) if marker is not None and marker.incremental_from else None
    full = next((r for r in reviews if link and r.get("url") == link.group(1)), None)
    return [review] if full is None else [review, full]


def reviewer_reviews(repo: str, number: int, reviewer: str) -> list[dict]:
    """Every Projector review the reviewer posted on the pull request, from every page, each with
    its `id`, `url`, `at`, and `body`."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                   "--jq", f'.[] | select(.user.login == "{reviewer}") '
                           '| select(.body // "" | contains("projector-review")) '
                           '| {id, url: .html_url, at: .submitted_at, body} | @json']).splitlines()
    return [json.loads(row) for row in rows if row.strip()]


def standing_change_requests(repo: str, number: int, reviewer: str) -> list[dict]:
    """The reviewer's Projector `REQUEST_CHANGES` reviews that still hold `reviewDecision`, each with its
    `id` and `url`: those whose body carries a verdict marker, which nobody dismissed and no later
    approval by the account replaced."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                   "--jq", f'.[] | select(.user.login == "{reviewer}") '
                           '| select(.state == "APPROVED" or .state == "CHANGES_REQUESTED") '
                           '| {id, url: .html_url, state, body} | @json']).splitlines()
    standing = []
    for review in (json.loads(row) for row in rows if row.strip()):
        if review["state"] == "APPROVED":
            standing = []
        elif review["state"] == "CHANGES_REQUESTED" and verdict_marker(review.get("body") or "") is not None:
            standing.append({"id": review["id"], "url": review["url"]})
    return standing


def dismiss(repo: str, number: int, reviewer: str, requests: list[dict], message: str, token: str) -> list[int]:
    """Dismiss each change request as the reviewer, then read them back, and return their ids."""
    for request in requests:
        try:
            run_gh(["api", "-X", "PUT", f"repos/{repo}/pulls/{number}/reviews/{request['id']}/dismissals",
                    "-f", f"message={message}", "-f", "event=DISMISS"], token)
        except ReviewError as exc:
            raise ReviewError(f"could not dismiss change request {request['id']} ({exc}); dismiss it at "
                              f"{request['url']} and run publish again") from exc
    ids = [request["id"] for request in requests]
    left = [request for request in standing_change_requests(repo, number, reviewer) if request["id"] in ids]
    if left:
        raise ReviewError(f"change request {left[0]['id']} still stands after its dismissal; dismiss it at "
                          f"{left[0]['url']} and run publish again")
    return ids


def read_record(root: Path, loop: str) -> list[dict]:
    record = loop_record(root, loop)
    return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else []


def check_collision(root: Path, state: dict, loop: Optional[str], second: Optional[int], body: str,
                    reviews: list[dict]) -> None:
    """The collision check review-pr § Publish one review describes, read from the loop's record.

    `reviews` is the reviewer's listing from `reviewer_reviews`. A verdict on the head is any whose
    marker names it, in this loose form so a marker in an older shape still collides. Only a verdict by
    this model collides, or one whose marker names no model, since another model is a second opinion."""
    on_head = re.compile(f"projector-review .* sha={state['sha']}.*")
    model = identity(state["model"])

    def same_model(body: str) -> bool:
        marker = on_head.search(body)
        named = marker and re.search(r"\bmodel=(\S+)", marker.group(0))
        return bool(marker) and (not named or identity(named.group(1)) == model)

    earlier = [r["id"] for r in reviews if same_model(r.get("body") or "")]
    if loop is not None:
        mine = {r["review_id"] for r in read_record(root, loop)
                if r.get("repo") == state["repo"] and r.get("number") == state["number"]}
        others = [i for i in earlier if i not in mine]
        if others:
            raise ReviewError(
                f"the reviewer already has a verdict by model {state['model']} on {state['sha'][:7]} that loop "
                f"{loop} did not publish (review {', '.join(map(str, others))}); another loop of the same model is "
                "reviewing this pull request, so ask the user which one continues")
        if any(i in mine for i in earlier) and not state.get("rereview"):
            raise ReviewError(
                f"loop {loop} already published a verdict on {state['sha'][:7]}; a re-review of this head "
                f"starts with `project review setup {state['number']} --rereview`")
        return
    if earlier:
        if second not in earlier:
            raise ReviewError(
                f"the reviewer already has a verdict by model {state['model']} on {state['sha'][:7]} "
                f"(review {', '.join(map(str, earlier))}); ask the user, and if they want a second one pass "
                "--second-verdict with that review's id")
        if str(second) not in body:
            raise ReviewError(f"a second verdict must name the earlier review {second} in its body")


# The newest verdict

# Anyone can review a public repository's pull request, so there, beside the reviewer's own, a verdict
# counts only from an account the repository lets in. Only an account that can see a private or internal
# repository can review its pull requests, so there every review counts. That matters because GitHub hides
# a private organization membership from a token that cannot see it, such as a workflow's GITHUB_TOKEN, and
# reports that member's reviews with a lower association.
TRUSTED = ("OWNER", "MEMBER", "COLLABORATOR")
CLOSED = ("private", "internal")


def review_counts(association: Optional[str], visibility: Optional[str]) -> bool:
    """Whether a review whose author has `association` can sign a pull request off or set its status on the
    site, in a repository of `visibility`: GitHub's `public`, `private`, or `internal`, in either case, or
    None when it is unknown. An unknown visibility counts as public."""
    return (visibility or "").lower() in CLOSED or association in TRUSTED


def repo_visibility(pr: dict) -> Optional[str]:
    """The visibility of the repository that `pr`, a pull request as GitHub's REST API returns it, targets,
    or None when it does not say."""
    return ((pr.get("base") or {}).get("repo") or {}).get("visibility")


def verdicts(repo: str, number: int, reviewer: str, visibility: Optional[str]) -> list[dict]:
    """Every Projector review on the pull request that can be its head's newest verdict, from every page:
    the reviewer's, and any other that `review_counts` counts in a repository of `visibility`. Each has its
    `id`, `url`, `at`, `login`, and `body`."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                   "--jq", '.[] | select(.body // "" | contains("projector-review")) '
                           '| {id, url: .html_url, at: .submitted_at, login: .user.login, '
                           'association: .author_association, body} | @json']).splitlines()
    found = [json.loads(row) for row in rows if row.strip()]
    return [r for r in found
            if (r.get("login") or "").lower() == reviewer.lower() or review_counts(r.get("association"), visibility)]


def newest_verdicts(head: str, reviews: list[dict]) -> list[dict]:
    """Each account's newest Projector verdict by each model on `head` among `reviews`, newest first, so the
    first is the head's newest verdict, whichever account and model gave it. Each has the `model`, by
    identity, its `verdict`, the `review` id, `url`, `at`, and `login` of the review that gave it, and how
    many `suggestions` stand with that review, as `with_full_review` finds them.

    One entry per account, not per model, because a reviewer is a model within an account: a teammate whose
    loop runs the same model must not hide the verdict a review loop looks for as its own."""
    newest: dict[tuple[str, str], tuple[dict, dict]] = {}

    def order(entry: dict) -> tuple[datetime, int]:
        return parse_time(entry["at"]), entry["review"] or 0

    for found in reviews:
        marker = verdict_marker(found.get("body") or "")
        if marker is None or marker.sha != head or not found.get("at"):
            continue
        entry = {"model": identity(marker.model), "verdict": marker.verdict, "review": found.get("id"),
                 "url": found.get("url"), "at": found["at"], "login": found.get("login")}
        key = (entry["model"], (entry["login"] or "").lower())
        known = newest.get(key)
        if known is None or order(entry) >= order(known[0]):
            newest[key] = (entry, found)
    for entry, found in newest.values():
        entry["suggestions"] = sum(map(listed_suggestions, with_full_review(found, reviews)))
    return sorted((entry for entry, _ in newest.values()), key=order, reverse=True)


def suggesting_reviews(head: str, reviews: list[dict]) -> list[dict]:
    """The reviews among `reviews` whose suggestions `fix-pr` decides on `head`: those `with_full_review` finds
    for each verdict `newest_verdicts` gives, newest first, each once."""
    by_id = {r.get("id"): r for r in reviews}
    chosen = {found.get("id"): found for entry in newest_verdicts(head, reviews)
              for found in with_full_review(by_id[entry["review"]], reviews)}
    return list(chosen.values())


FINDING_MODEL = re.compile(r"<!--\s*projector-finding\b[^>]*?\smodel=(\S+) -->")


def thread_model(thread: dict) -> Optional[str]:
    """The model a finding thread's marker names, or None for one an earlier release opened."""
    found = FINDING_MODEL.search(thread["comments"][0]["body"]) if thread["comments"] else None
    return found.group(1) if found else None


def status(repo: str, number: int, reviewer: str) -> dict:
    """The newest Projector verdict on the pull request's current head, the verdict `publish` sets draft
    state by, with each account's newest verdict by each model on it, every open Projector finding thread,
    and every open hold comment. Its `suggestions` count those `review suggestions` lists for the head."""
    pr = read_pull_request(repo, number)
    head = pr["head"]["sha"]
    found = verdicts(repo, number, reviewer, repo_visibility(pr))
    models = newest_verdicts(head, found)
    threads = [t for t in census(repo, number)["threads"] if not t["resolved"]]
    held = holds(repo, number, (pr.get("user") or {}).get("login") or "")
    newest = models[0] if models else None
    return {"repo": repo, "number": number, "head": head, "draft": bool(pr.get("draft")),
            "verdict": newest["verdict"] if newest else "unreviewed", "newest": newest, "models": models,
            "suggestions": sum(map(listed_suggestions, suggesting_reviews(head, found))),
            "open_threads": [{"path": t["path"], "line": t["line"], "model": thread_model(t)} for t in threads],
            "held": bool(held), "holds": held}


def status_lines(result: dict) -> list[str]:
    """`review status` without --json: whether the head is held, the newest verdict on it, each account's
    and model's when several gave one, naming the account where two share a model, each with the number of
    suggestions standing with it when there are any, the open threads, and the hold comments."""
    newest = result["newest"]
    held = "held; " if result["held"] else ""
    count = newest["suggestions"] if newest else 0
    listing = f" with {count} suggestion{'' if count == 1 else 's'}" if count else ""
    lines = [f"#{result['number']} at {result['head'][:7]}: {held}{result['verdict']}{listing}"
             + (f" from {newest['model']}, {newest['url'] or 'review ' + str(newest['review'])}" if newest else "")]
    rows = result["models"]
    if len(rows) > 1:
        shared = Counter(row["model"] for row in rows)
        names = [row["model"] + (f" ({row['login']})" if shared[row["model"]] > 1 else "") for row in rows]
        width = max(len(name) for name in names) + 3
        for row, name in zip(rows, names):
            at = parse_time(row["at"]).strftime("%H:%M UTC")
            count = row["suggestions"]
            listing = f", {count} suggestion{'' if count == 1 else 's'}" if count else ""
            lines.append(f"  {name:<{width}}{row['verdict']:<20}review {row['review']}, {at}{listing}")
    threads = result["open_threads"]
    if threads:
        named = ", ".join(f"{t['path']}:{t['line']}" + (f" ({t['model']})" if t["model"] else "") for t in threads)
        lines.append(f"  {len(threads)} open finding thread{'' if len(threads) == 1 else 's'}: {named}")
    for hold in result["holds"]:
        lines.append(f"  held in draft by comment {hold['id']} until the changes it lists are pushed")
    return lines


def suggestions(repo: str, number: int, reviewer: str) -> list[dict]:
    """`review suggestions`: the suggestions `fix-pr` decides on the pull request's current head, from the
    reviews `suggesting_reviews` picks among those `status` counts, which `status` counts them from too. Each
    has its review's `url` and the head `sha` that review's marker names, and the item's `path`, `line`, and
    `text`."""
    pr = read_pull_request(repo, number)
    reviews = suggesting_reviews(pr["head"]["sha"], verdicts(repo, number, reviewer, repo_visibility(pr)))
    return [{"url": r.get("url"), "sha": verdict_marker(r.get("body") or "").sha, **item}
            for r in reviews for item in parse_suggestions(r.get("body") or "") or []]


def suggestions_text(items: list[dict]) -> str:
    """`review suggestions` without --json: a block for each suggestion, naming the place it names and the
    head of the review it comes from, with its text below."""
    return "\n\n".join(f"{place(item) if item['path'] else '(no file named)'}, in the review of {item['sha'][:7]}: "
                       f"{item['url']}\n{item['text']}" for item in items)


def check_body(body: str) -> None:
    if not body.strip():
        raise ReviewError("the body is empty; write the review below the signature line")
    if OWN_MARKER.search(body) or body.lstrip().startswith(MARK):
        raise ReviewError("leave the signature line and markers out of the body; publish writes them")


def prose(body: str) -> str:
    """The body without its quoted code, keeping each span that holds a placeholder alone."""
    return CODE.sub(lambda m: m.group(0) if LONE_PLACEHOLDER.fullmatch(m.group(0)) else "", body)


def fill(body: str, values: dict[str, str]) -> str:
    """The body with each placeholder in `values` filled, and any other left as written.

    Placeholders are filled only in prose and in a span that holds one alone, so quoted code
    keeps its braces; anything else in braces is the author's text, not a placeholder."""
    def replace(match: re.Match) -> str:
        return values.get(match.group(1), "{" + match.group(1) + "}")

    pieces, last = [], 0
    for code in CODE.finditer(body):
        pieces.append(PLACEHOLDER.sub(replace, body[last:code.start()]))
        lone = LONE_PLACEHOLDER.fullmatch(code.group(0))
        pieces.append(f"`{replace(lone)}`" if lone else code.group(0))
        last = code.end()
    pieces.append(PLACEHOLDER.sub(replace, body[last:]))
    return "".join(pieces)


def heading(state: dict, verdict: str, findings: int, covered: str, at: datetime,
            incremental_from: Optional[str] = None) -> tuple[str, str, dict[str, str]]:
    """The signature line, the marker, and the placeholders' values, from one clock reading, so the
    duration and `seconds=` cannot disagree."""
    seconds = elapsed(state["start_comment"]["created_at"], at)
    took = duration(seconds, state["heads"])
    version, effort = distribution_version(), live_effort()
    signature = (f"{MARK} **Projector review** of `{state['sha'][:7]}` · "
                 f"{producer_segments(version, state['model'], effort)}"
                 f"**{VERDICT_WORDS[verdict]}** · "
                 + (f"incremental from `{incremental_from[:7]}` · " if incremental_from else "")
                 + f"took {took}")
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector={version} model={state['model']} "
              + (f"effort={effort} " if effort else "")
              + f"sha={state['sha']} findings={findings} seconds={seconds} covered={covered} -->")
    if not SIGNATURE.match(signature) or not MARKER.match(marker):
        raise ReviewError("the signature line or marker does not match review-pr/SKILL.md; check the "
                          f"model in the review's state ({state['model']})")
    values = {"took": took, "seconds": str(seconds), "sha": state["sha"], "short_sha": state["sha"][:7]}
    return signature, marker, values


def compose(state: dict, verdict: str, body: str, covered: str, findings: int, census_line: str,
            at: datetime, entries: list[dict]) -> str:
    """The review's text: the signature line and marker, then the body with its placeholders filled, then
    the section listing the suggestions `entries` gives. A body that lists suggestions of its own, as one an
    older skill wrote, publishes as written, but never beside `entries`. Either way the line that counts the
    suggestions comes directly before their label."""
    check_body(body)
    if "{census}" not in prose(body):
        raise ReviewError("the body must print the census the verdict rests on; put {census} where it goes, "
                          "outside code")
    if entries and parse_suggestions(body) is not None:
        raise ReviewError("the body has a suggestions section of its own, and the threads file gives P3 entries; "
                          "take the section out of the body, and publish lists the P3 entries in its place")
    signature, marker, values = heading(state, verdict, findings, covered, at)
    filled = fill(body, dict(values, census=census_line)).strip()
    if entries:
        filled += f"\n\n{suggestions_section(entries)}"
    return f"{signature}\n\n{marker}\n\n{with_suggestions_count(filled)}\n"


def listed(items: list[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


# The link `incremental_lead` writes to the full review an incremental review builds on.
FULL_REVIEW_LINK = re.compile(r"since the full review of \[`[0-9a-f]{7}`\]\((\S+?)\)")


def incremental_lead(rules: dict, base_ref: str) -> str:
    """The sentence that opens an incremental review: the full review it builds on, how the head
    moved since, the size of the change, and any conflict the author resolved."""
    across = "" if rules["kind"] == "push" else f" across a {KINDS[rules['kind']].format(base=f'`{base_ref}`')}"
    sentence = (f"Incremental review of the change since the full review of [`{rules['from'][:7]}`]"
                f"({rules['review']}){across}: {change_size(rules)}")
    conflicts = [f"`{path}`" for path in rules["conflicts"]]
    if conflicts:
        sentence += (f", with {'a resolved conflict' if len(conflicts) == 1 else 'resolved conflicts'} in "
                     f"{listed(conflicts)}")
    return sentence + "."


def compose_incremental(state: dict, body: str, rules: dict, cross_author: bool, at: datetime) -> str:
    """An incremental review's text: the signature line, the marker and the incremental line, the
    lead, and the reviewer's short body, which may hold every placeholder but `{census}`."""
    check_body(body)
    if "{census}" in prose(body):
        raise ReviewError("an incremental review prints no census; take {census} out of the body")
    count = len(rules["files"])
    signature, marker, values = heading(state, "clean", 0, f"{count}/{count}", at, rules["from"])
    filled = fill(body, values).strip()
    if len(filled) > INCREMENTAL_BODY_LIMIT:
        raise ReviewError(f"the body holds {len(filled)} characters once filled, and an incremental review's "
                          f"holds at most {INCREMENTAL_BODY_LIMIT}; a change that needs more is a full review's")
    incremental = (f"<!-- projector-incremental v=1 from={rules['from']} kind={rules['kind']} "
                   f"insertions={rules['insertions']} deletions={rules['deletions']} files={count} "
                   f"conflicts={len(rules['conflicts'])} -->")
    closing = "\n\nA human approval is what remains." if cross_author else ""
    return (f"{signature}\n\n{marker}\n{incremental}\n\n{incremental_lead(rules, state['base_ref'])}\n\n"
            f"{filled}{closing}\n")


def publish(paths: Paths, loop: Optional[str], verdict: Optional[str], body_path: Path,
            threads_path: Optional[Path], covered: Optional[str], second: Optional[int], allow_approve: bool,
            incremental: bool = False, enabled: bool = True) -> dict:
    """Submit the review, then re-read it, act on the head's newest verdict and its open finding
    threads, record, delete the start comment, and release the lock, in that order. Every refusal
    leaves the lock held for this review to retry.

    Acting on the newest verdict sets a self-review's draft state. On another author's pull request a
    clean newest verdict with no finding open dismisses the reviewer's standing Projector change
    requests, because a `COMMENT` review leaves `reviewDecision` where the last of them put it. While
    the author holds the pull request, it stays a draft and its change requests stand.

    With `incremental`, the review is a clean `COMMENT` that builds on this loop's last full
    review, published only while `incremental_rules` passes, with `enabled` from
    `review.incremental`."""
    root, number = paths.root, paths.number
    state = read_state(paths)
    loop = loop if loop is not None else state.get("loop")
    repo, sha, reviewer = state["repo"], state["sha"], state["reviewer"]
    if incremental:
        for given, flag in ((threads_path, "--threads"), (covered, "--covered"), (second, "--second-verdict")):
            if given is not None:
                raise ReviewError(f"--incremental takes no {flag}: an incremental review opens no thread, covers "
                                  "the files its change touches, and is never a second verdict")
        if verdict not in (None, "clean"):
            raise ReviewError("an incremental review is always clean; a problem the read finds goes to a full "
                              "review, which files it")
        verdict = "clean"
    else:
        if verdict not in VERDICT_WORDS:
            raise ReviewError("--verdict must be clean or changes-requested" + (f", not {verdict}" if verdict else ""))
        if not re.fullmatch(r"\d+/\d+", covered or ""):
            raise ReviewError("--covered must be <files read>/<files changed>, such as 12/12")
    if not paths.lock(sha).exists():
        raise ReviewError(f"no review of {repo}#{number} at {sha[:7]} holds the lock; "
                          f"run `project review setup {number}` again")
    published = state.get("published")
    if published and published.get("sha") == sha and state.get("start_comment"):
        # A publish that submitted and then stopped: finish it rather than post a second review.
        review_id = published["id"]
    else:
        if not body_path.is_file():
            raise ReviewError(f"the body file {body_path} does not exist; compose the review there first")
        body = body_path.read_text(encoding="utf-8")
        threads = load_threads(threads_path)
        entries = suggestion_entries(threads)
        pr = pull_request(repo, number)
        if pr["head"]["sha"] != sha:
            raise ReviewError(f"the head moved to {pr['head']['sha'][:7]} since {sha[:7]} was set up; "
                              f"run `project review move {number}` and review the new head")
        reviews = reviewer_reviews(repo, number, reviewer)
        check_collision(root, state, loop, second, body, reviews)
        incremental_from = None
        mine = ((pr.get("user") or {}).get("login") or "").lower() == reviewer.lower()
        if incremental:
            rules = incremental_rules(root, state, loop, enabled, reviews)
            if not rules["incremental"]:
                raise ReviewError(f"an incremental review is refused: {rules['reason']}; run the full review "
                                  "on this setup")
            incremental_from = rules["from"]
            text = compose_incremental(state, body, rules, not mine, now())
            event = "COMMENT"
        else:
            check_threads(threads, diff_lines(repo, number) if any(not is_suggestion(t) for t in threads) else {})
            threads = [t for t in threads if not is_suggestion(t)]
            tally = census(repo, number)
            open_now = tally["open"] + len(threads)
            if verdict == "clean" and open_now:
                raise ReviewError(f"a clean verdict needs no open finding, and {tally['open']} are open and "
                                  f"{len(threads)} are being posted; settle them or request changes")
            if verdict == "changes-requested" and not open_now:
                raise ReviewError("changes-requested needs an open finding or a new one; with none open the head "
                                  "is clean")
            total = tally["total"] + len(threads)
            noun = "thread" if total == 1 else "threads"
            census_line = f"{total} finding {noun}: {tally['resolved']} resolved, {open_now} open"
            text = compose(state, verdict, body, covered, len(threads), census_line, now(), entries)
            if mine or verdict == "clean" and not allow_approve:
                event = "COMMENT"
            elif verdict == "changes-requested":
                event = "REQUEST_CHANGES"
            else:
                # An approval would clear the pull request to merge while its author is still building
                # changes for it.
                event = "COMMENT" if holds(repo, number, (pr.get("user") or {}).get("login") or "") else "APPROVE"
        payload = {"commit_id": sha, "event": event, "body": text,
                   "comments": [{"path": t["path"], "line": t["line"], "side": "RIGHT",
                                 "body": f"<!-- projector-finding v=1 priority={t['priority']} sha={sha} "
                                         f"model={state['model']} -->\n{t['body'].strip()}\n"} for t in threads]}
        token = reviewer_token(reviewer)
        paths.folder.mkdir(parents=True, exist_ok=True)
        paths.request.write_text(json.dumps(payload), encoding="utf-8")
        posted = gh_json(["api", "-X", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input",
                          str(paths.request)], token)
        paths.request.unlink(missing_ok=True)
        review_id = posted["id"]
        # Recorded before anything else can fail, so a retry finishes this review rather than
        # posting a second one.
        state["published"] = {"id": review_id, "sha": sha, "verdict": verdict, "event": event,
                              "from": incremental_from, "self": mine}
        write_state(paths, state)
    published = state["published"]
    token = reviewer_token(reviewer)
    read = gh_json(["api", f"repos/{repo}/pulls/{number}/reviews/{review_id}"]) or {}
    if f"sha={sha}" not in (read.get("body") or ""):
        raise ReviewError(f"review {review_id} does not carry sha={sha[:7]} when read back; check it on GitHub")
    newest, draft_wanted, open_threads, dismissed, held = None, None, None, [], None
    standing = [] if published.get("self") else standing_change_requests(repo, number, reviewer)
    if published.get("self") or standing:
        # The head's newest Projector verdict decides, whichever model gave it: this review, counted as read
        # back whether or not the listing shows it yet, or one another model posted since. A finding thread
        # open now holds the pull request whatever that verdict says, because another model can post one
        # after this review counted none and before its own verdict landed. So does the author's hold: the
        # author is building changes no review has read.
        posted = {"id": review_id, "url": read.get("html_url"), "at": read.get("submitted_at") or now().isoformat(),
                  "login": reviewer, "body": read["body"]}
        current = gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}
        others = [r for r in verdicts(repo, number, reviewer, repo_visibility(current)) if r.get("id") != review_id]
        newest = newest_verdicts(sha, others + [posted])[0]
        open_threads = census(repo, number)["open"]
        held = bool(holds(repo, number, (current.get("user") or {}).get("login") or ""))
    if standing and newest["verdict"] == "clean" and not open_threads and not held:
        dismissed = dismiss(repo, number, reviewer, standing,
                            f"{MARK} The newest verdict on {sha[:7]} is clean: {newest['url']}", token)
    if published.get("self"):
        draft_wanted = newest["verdict"] == "changes-requested" or open_threads > 0 or held
        draft = bool(current.get("draft"))
        if draft != draft_wanted:
            run_gh(["pr", "ready", str(number), "--repo", repo, *(["--undo"] if draft_wanted else [])], token)
            draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
            if draft != draft_wanted:
                raise ReviewError(f"{repo}#{number} is {'a draft' if draft else 'ready'} after the newest verdict, "
                                  f"{newest['verdict']}, with {open_threads} open finding threads"
                                  f"{' and a hold open' if held else ''}; set it with `gh pr ready` and retry")
    if loop is not None:
        record = read_record(root, loop)
        if not any(r.get("review_id") == review_id for r in record):
            # `base` is the merge base a later incremental review measures from; `from` marks an
            # incremental review, which a later one never builds on.
            record.append({"repo": repo, "number": number, "sha": sha, "review_id": review_id,
                           "verdict": published["verdict"], "published_at": now().isoformat(),
                           "base": state["base"], "from": published.get("from"), "model": state["model"]})
            loop_record(root, loop).parent.mkdir(parents=True, exist_ok=True)
            loop_record(root, loop).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    comment = (state.get("start_comment") or {}).get("id")
    if comment:
        delete_comment(repo, comment, token)
        state["start_comment"] = None
    write_state(paths, state)
    paths.lock(sha).unlink(missing_ok=True)
    return {"repo": repo, "number": number, "sha": sha, "review_id": review_id, "verdict": published["verdict"],
            "event": published["event"], "draft": draft_wanted, "dismissed": dismissed, "from": published.get("from"),
            "newest": newest, "open_threads": open_threads, "held": held}
