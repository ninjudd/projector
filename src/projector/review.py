"""The mechanical steps of a review: set up a head, follow a moved head, count findings.

`review-changes` does its judgment in prose and its bookkeeping here, so the
checks a hand-built helper kept skipping run every time. Every SHA comes from
GitHub or the review's state file, never from an argument; the repository is
passed to `gh` explicitly, so a command runs from any directory; a comment is
posted as the reviewer with a token for that call alone, never by switching
the account the rest of the session uses; and every refusal raises
`ReviewError`, which exits non-zero with what to do about it.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .core import ProjectorError
from .summary import repo_slug

FINDING_MARKER = "projector-finding"
START_MARK = "⚬◀"


class ReviewError(ProjectorError):
    """A review step refused; its message says why and what to do."""

    exit_code = 1


# GitHub

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


class NotFound(ReviewError):
    pass


def gh_json(args: list[str], token: Optional[str] = None):
    return json.loads(run_gh(args, token) or "null")


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise ReviewError(f"git {' '.join(args)} failed: {exc.stderr.strip()}") from exc


# Identity

def reviewer_login(explicit: Optional[str], configured: Optional[str]) -> str:
    """The reviewer: explicit input, then review.username, then the authenticated user."""
    for login in (explicit, configured):
        if login:
            return login
    login = run_gh(["api", "user", "--jq", ".login"]).strip()
    if not login:
        raise ReviewError("could not read the authenticated GitHub user; run `gh auth status`")
    return login


def reviewer_token(login: str) -> str:
    """A token for the reviewer, for one call; the session's active account is left alone."""
    try:
        token = run_gh(["auth", "token", "--user", login]).strip()
    except ReviewError as exc:
        raise ReviewError(f"no gh login for the reviewer {login}; run `gh auth login` for that account") from exc
    if not token:
        raise ReviewError(f"no gh login for the reviewer {login}; run `gh auth login` for that account")
    return token


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
        return self.root / self.repo

    @property
    def state(self) -> Path:
        return self.folder / f"{self.number}.json"

    def lock(self, sha: str) -> Path:
        return self.folder / f"{self.number}-{sha}.lock"

    def worktree(self, sha: str) -> Path:
        return self.folder / "worktrees" / f"{self.number}-{sha[:12]}"


def read_state(paths: Paths) -> dict:
    if not paths.state.is_file():
        raise ReviewError(f"no review of {paths.repo}#{paths.number} is set up here; run `project review setup {paths.number}`")
    return json.loads(paths.state.read_text(encoding="utf-8"))


def write_state(paths: Paths, state: dict) -> None:
    paths.folder.mkdir(parents=True, exist_ok=True)
    tmp = paths.state.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(paths.state)


def take_lock(paths: Paths, sha: str) -> Path:
    """Claim the review of this pull request at this head, or refuse when another instance holds it."""
    lock = paths.lock(sha)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ReviewError(
            f"another review of {paths.repo}#{paths.number} at {sha[:7]} holds {lock}; let it finish, "
            "or delete the lock if no review is running"
        ) from exc
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{os.getpid()}\n")
    return lock


# The pull request

def pull_request(repo: str, number: int) -> dict:
    """The open, same-repository pull request, or a refusal saying why it cannot be reviewed."""
    try:
        pr = gh_json(["api", f"repos/{repo}/pulls/{number}"])
    except NotFound as exc:
        raise ReviewError(f"{repo}#{number} does not exist or is not visible to this account") from exc
    if pr.get("state") != "open":
        raise ReviewError(f"{repo}#{number} is {pr.get('state') or 'not open'}; only an open pull request is reviewed")
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    if head_repo != repo:
        raise ReviewError(
            f"{repo}#{number} comes from {head_repo or 'a deleted fork'}; these commands review only a branch "
            "in the base repository — review a fork's head by reading, as review-changes describes"
        )
    return pr


def fetch_head(checkout: Path, number: int, sha: str, base_ref: str, base_sha: str) -> str:
    """Fetch the head and base into the checkout, verify the head, and return the merge base."""
    git("-C", str(checkout), "fetch", "--quiet", "--no-tags", "origin",
        f"refs/pull/{number}/head", f"refs/heads/{base_ref}")
    fetched = git("-C", str(checkout), "rev-parse", "FETCH_HEAD")
    if fetched != sha:
        raise ReviewError(f"the head moved to {fetched[:7]} while it was fetched; run the command again")
    return git("-C", str(checkout), "merge-base", sha, base_sha)


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
    lines = [f"{START_MARK} **Projector review started** · model `{model}` · effort `{effort}` · reviewing `{sha[:7]}`", ""]
    if moved_from:
        lines += [f"The head moved from `{moved_from[:7]}` to `{sha[:7]}`; this review is being updated for the new changes.", ""]
    lines.append(f"<!-- projector-start v=1 sha={sha} -->")
    return "\n".join(lines) + "\n"


# Commands

@dataclass
class Context:
    repo: str
    number: int
    paths: Paths
    checkout: Optional[Path]


def context(number: int, repo: Optional[str], checkout: Optional[Path], root: Optional[Path]) -> Context:
    """Resolve the repository from --repo or the checkout's origin; the checkout is needed only to fetch."""
    if checkout is not None:
        checkout = Path(git("-C", str(checkout), "rev-parse", "--show-toplevel"))
    if not repo:
        if checkout is None:
            raise ReviewError("run this inside a checkout of the repository, or pass --repo owner/name")
        repo = repo_slug(git("-C", str(checkout), "remote", "get-url", "origin"))
        if not repo:
            raise ReviewError("the checkout's origin is not a GitHub repository; pass --repo owner/name")
    return Context(repo, number, Paths(state_dir(root), repo, number), checkout)


def setup(ctx: Context, reviewer: str, model: str, effort: str) -> dict:
    if ctx.checkout is None:
        raise ReviewError("setting up a review needs a checkout to fetch into; run it there or pass --checkout")
    pr = pull_request(ctx.repo, ctx.number)
    sha, base_ref, base_sha = pr["head"]["sha"], pr["base"]["ref"], pr["base"]["sha"]
    lock = take_lock(ctx.paths, sha)
    try:
        merge_base = fetch_head(ctx.checkout, ctx.number, sha, base_ref, base_sha)
        worktree = ctx.paths.worktree(sha)
        add_worktree(ctx.checkout, worktree, sha)
        token = reviewer_token(reviewer)
        posted = gh_json(["api", f"repos/{ctx.repo}/issues/{ctx.number}/comments",
                          "-f", f"body={start_comment(model, effort, sha)}"], token)
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    state = {
        "repo": ctx.repo,
        "number": ctx.number,
        "sha": sha,
        "base": merge_base,
        "base_ref": base_ref,
        "worktree": str(worktree),
        "worktrees": [str(worktree)],
        "checkout": str(ctx.checkout),
        "reviewer": reviewer,
        "model": model,
        "effort": effort,
        "start_comment": {"id": posted["id"], "created_at": posted["created_at"]},
        "heads": 1,
    }
    write_state(ctx.paths, state)
    return state


def move(ctx: Context) -> dict:
    state = read_state(ctx.paths)
    checkout = ctx.checkout or Path(state["checkout"])
    pr = pull_request(ctx.repo, ctx.number)
    sha, old = pr["head"]["sha"], state["sha"]
    if sha == old:
        raise ReviewError(f"the head of {ctx.repo}#{ctx.number} is still {old[:7]}; there is nothing to move")
    lock = take_lock(ctx.paths, sha)
    try:
        merge_base = fetch_head(checkout, ctx.number, sha, pr["base"]["ref"], pr["base"]["sha"])
        worktree = ctx.paths.worktree(sha)
        add_worktree(checkout, worktree, sha)
        token = reviewer_token(state["reviewer"])
        comment = state["start_comment"]["id"]
        body = start_comment(state["model"], state["effort"], sha, moved_from=old)
        run_gh(["api", "-X", "PATCH", f"repos/{ctx.repo}/issues/comments/{comment}", "-f", f"body={body}"], token)
        reread = gh_json(["api", f"repos/{ctx.repo}/issues/comments/{comment}"])
        if f"sha={sha}" not in (reread or {}).get("body", ""):
            raise ReviewError(f"the start comment {comment} still does not name {sha[:7]} after the edit; edit it by hand")
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    ctx.paths.lock(old).unlink(missing_ok=True)
    state.update(sha=sha, base=merge_base, base_ref=pr["base"]["ref"], worktree=str(worktree),
                 heads=state["heads"] + 1, worktrees=state["worktrees"] + [str(worktree)])
    write_state(ctx.paths, state)
    return state


THREADS_QUERY = """query($o:String!,$r:String!,$n:Int!,$after:String){ repository(owner:$o,name:$r){
  pullRequest(number:$n){ reviewThreads(first:100, after:$after){
    pageInfo{ hasNextPage endCursor }
    nodes{ id isResolved isOutdated path line originalLine
      comments(first:50){ nodes{ databaseId body } } } } } } }"""


def census(ctx: Context) -> dict:
    """Every Projector finding thread on the pull request, read to the last page, with its
    replies, so the review can verify and settle each one from this one list."""
    owner, name = ctx.repo.split("/", 1)
    threads, after = [], None
    while True:
        args = ["api", "graphql", "-f", f"query={THREADS_QUERY}", "-f", f"o={owner}", "-f", f"r={name}",
                "-F", f"n={ctx.number}"]
        if after:
            args += ["-f", f"after={after}"]
        page = gh_json(args)
        pr = ((page or {}).get("data") or {}).get("repository", {}).get("pullRequest")
        if pr is None:
            raise ReviewError(f"{ctx.repo}#{ctx.number} does not exist or is not visible to this account")
        connection = pr["reviewThreads"]
        for node in connection["nodes"]:
            first = (node.get("comments") or {}).get("nodes") or [{}]
            if FINDING_MARKER in (first[0].get("body") or ""):
                threads.append({
                    "id": node["id"],
                    "path": node.get("path"),
                    "line": node.get("line") if node.get("line") is not None else node.get("originalLine"),
                    "outdated": bool(node.get("isOutdated")),
                    "resolved": bool(node.get("isResolved")),
                    "comments": [{"id": c.get("databaseId"), "body": c.get("body") or ""}
                                 for c in (node.get("comments") or {}).get("nodes") or []],
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

