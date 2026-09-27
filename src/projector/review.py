"""The mechanical steps of a review: set up a head, follow a moved head, count findings.

`review-changes` does its judgment in prose and its bookkeeping here, so the
checks a hand-built helper kept skipping run every time. Every SHA comes from
GitHub or the review's state file, never from an argument; the repository is
passed to `gh` explicitly, so a command runs from any directory; a comment is
posted as the reviewer with a token for that call alone, never by switching
the account the rest of the session uses; and every refusal raises
`ReviewError`, which exits non-zero with what to do about it.

Layout of the state directory:

    prs/<owner>/<repo>/<pr>.json         the review's state
    prs/<owner>/<repo>/<pr>-<sha>.lock   the lock a review holds from setup until publish
    worktrees/<owner>/<repo>/<pr>/<sha>  scratch worktrees, never inside the checkout
    loops/<id>/published.json            a loop's record of the review ids it published
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .core import ProjectorError
from .summary import repo_slug

FINDING_MARKER = "projector-finding"
START_MARK = "📽️"
SHORT = 12
STALE_AFTER = timedelta(days=1)
LOOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    """Whether the head's code may run, by review-changes § Run a head's code only when the
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
    root: Path
    repo: str
    number: int

    @property
    def folder(self) -> Path:
        return self.root / "prs" / self.repo

    @property
    def state(self) -> Path:
        return self.folder / f"{self.number}.json"

    def lock(self, sha: str) -> Path:
        return self.folder / f"{self.number}-{sha}.lock"

    def locks(self) -> list[Path]:
        return sorted(self.folder.glob(f"{self.number}-*.lock")) if self.folder.is_dir() else []

    def worktree(self, sha: str) -> Path:
        return self.root / "worktrees" / self.repo / str(self.number) / sha[:SHORT]


def loop_record(root: Path, loop: str) -> Path:
    return root / "loops" / loop / "published.json"


def ensure_loop(root: Path, loop: Optional[str]) -> None:
    if loop is None:
        return
    record = loop_record(root, loop)
    if not record.exists():
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text("[]\n", encoding="utf-8")


def read_state(paths: Paths) -> dict:
    if not paths.state.is_file():
        raise ReviewError(f"no review of {paths.repo}#{paths.number} is set up here; "
                          f"run `project review setup {paths.number}`")
    return json.loads(paths.state.read_text(encoding="utf-8"))


def write_state(paths: Paths, state: dict) -> None:
    paths.folder.mkdir(parents=True, exist_ok=True)
    tmp = paths.state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(paths.state)


def now() -> datetime:
    return datetime.now(timezone.utc)


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
            f"another review of {paths.repo}#{paths.number} at {sha[:7]} holds its lock{holder}; let it finish, "
            f"or run `project review release {paths.number}` if no review is running"
        ) from exc
    with os.fdopen(fd, "w") as handle:
        json.dump({"sha": sha, "host": socket.gethostname(), "loop": loop, "pid": os.getpid(),
                   "taken_at": now().isoformat()}, handle)
        handle.write("\n")
    return lock


def release(paths: Paths) -> list[Path]:
    """Clear every lock this pull request's reviews hold; returns what was cleared."""
    released = paths.locks()
    for lock in released:
        lock.unlink(missing_ok=True)
    return released


# The pull request

def pull_request(repo: str, number: int) -> dict:
    """The open pull request, or a refusal saying why it cannot be reviewed."""
    try:
        pr = gh_json(["api", f"repos/{repo}/pulls/{number}"])
    except NotFound as exc:
        raise ReviewError(f"{repo}#{number} does not exist or is not visible to this account") from exc
    if pr.get("state") != "open":
        raise ReviewError(f"{repo}#{number} is {pr.get('state') or 'not open'}; only an open pull request is reviewed")
    return pr


def fetch(checkout: Path, ref: str) -> str:
    git("-C", str(checkout), "fetch", "--quiet", "--no-tags", "origin", ref)
    return git("-C", str(checkout), "rev-parse", "FETCH_HEAD")


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
        raise ReviewError(f"the scratch worktree {path} is at {head[:7]}, not {sha[:7]}; remove it and run again")


# Start comments

def start_comment(model: str, effort: str, sha: str, moved_from: Optional[str] = None) -> str:
    lines = [f"{START_MARK} **Projector review started** · model `{model}` · effort `{effort}` · "
             f"reviewing `{sha[:7]}`", ""]
    if moved_from:
        lines += [f"The head moved from `{moved_from[:7]}` to `{sha[:7]}`; this review is being updated "
                  "for the new changes.", ""]
    lines.append(f"<!-- projector-start v=1 sha={sha} -->")
    return "\n".join(lines) + "\n"


# Resolving which review a command means

def valid_loop(loop: Optional[str]) -> Optional[str]:
    if loop is not None and not LOOP_ID.match(loop):
        raise ReviewError(f"--loop {loop!r} is not a usable id; use letters, digits, '.', '_' and '-'")
    return loop


def find_repo(root: Path, number: int, repo: Optional[str], cwd: Path) -> str:
    """The repository a command after setup means: --repo, else the review set up here for
    this pull request, else the origin of the checkout the command runs in."""
    if repo:
        return repo
    found = sorted((root / "prs").glob(f"*/*/{number}.json")) if (root / "prs").is_dir() else []
    if len(found) == 1:
        return f"{found[0].parent.parent.name}/{found[0].parent.name}"
    if len(found) > 1:
        names = ", ".join(f"{p.parent.parent.name}/{p.parent.name}" for p in found)
        raise ReviewError(f"reviews of #{number} are set up in {names}; pass --repo to pick one")
    try:
        return origin_repo(checkout_root(cwd))
    except ReviewError as exc:
        raise ReviewError(f"no review of #{number} is set up here; pass --repo owner/name") from exc


# Commands

def setup(root: Path, number: int, checkout: Path, repo: Optional[str], reviewer: Optional[str],
          configured_reviewer: Optional[str], model: str, effort: str, loop: Optional[str],
          rereview: bool) -> dict:
    checkout = checkout_root(checkout)
    origin = origin_repo(checkout)
    if repo and repo.lower() != origin.lower():
        raise ReviewError(f"--repo {repo} is not this checkout's origin {origin}; run from a checkout of {repo}")
    repo = origin
    paths = Paths(root, repo, number)
    pr = pull_request(repo, number)
    sha = pr["head"]["sha"]
    lock = take_lock(paths, sha, loop)
    try:
        operator = authenticated_login()
        reviewer = reviewer_login(reviewer, configured_reviewer, operator)
        merge_base = fetch_head(checkout, repo, pr)
        worktree = paths.worktree(sha)
        add_worktree(checkout, worktree, sha)
        trusted, why = trusted_head(repo, pr, operator)
        token = reviewer_token(reviewer)
        posted = gh_json(["api", f"repos/{repo}/issues/{number}/comments",
                          "-f", f"body={start_comment(model, effort, sha)}"], token)
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
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
        "worktrees": [str(worktree)],
        "operator": operator,
        "reviewer": reviewer,
        "model": model,
        "effort": effort,
        "loop": loop,
        "rereview": rereview,
        "start_comment": {"id": posted["id"], "created_at": posted["created_at"]},
        "heads": 1,
    }
    write_state(paths, state)
    return state


def move(root: Path, number: int, repo: str, loop: Optional[str]) -> dict:
    paths = Paths(root, repo, number)
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
        body = start_comment(state["model"], state["effort"], sha, moved_from=old)
        run_gh(["api", "-X", "PATCH", f"repos/{state['repo']}/issues/comments/{comment}",
                "-f", f"body={body}"], token)
        reread = gh_json(["api", f"repos/{state['repo']}/issues/comments/{comment}"])
        if f"sha={sha}" not in (reread or {}).get("body", ""):
            raise ReviewError(f"the start comment {comment} still does not name {sha[:7]} after the edit; "
                              "edit it by hand")
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
