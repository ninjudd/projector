"""The mechanical steps of a review: set up a head, follow a moved head, count
findings, run the repository's gate, and publish.

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
# The mark that opens both the start comment and the review's signature line, as
# review-changes/SKILL.md § Label every review and finding writes it.
MARK = "📽️"
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


def gate(root: Path, number: int, repo: str, command: Optional[str], output_to_stderr: bool = False) -> dict:
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
    paths = Paths(root, repo, number)
    state = read_state(paths)
    if not state.get("trusted"):
        raise ReviewError(f"the head {state['sha'][:7]} of {state['repo']}#{number} is untrusted "
                          f"({state.get('untrusted_because') or 'no reason recorded'}), so its code does not "
                          "run; review it by reading")
    if not command:
        raise ReviewError("review.gate is not set in .projector.toml, so there is nothing to run; run the "
                          "repository's checks as review-changes describes")
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

def live_effort() -> str:
    """The reasoning effort the host reports for this session, or "" when it reports none.

    Claude Code sets CLAUDE_EFFORT for the Bash tool and keeps it current through
    a mid-session /effort change; a host that reports nothing, such as Codex or a
    model without effort support, leaves the label out rather than have it guessed.
    """
    return os.environ.get("CLAUDE_EFFORT", "").strip()


def effort_segment() -> str:
    effort = live_effort()
    return f"effort `{effort}` · " if effort else ""


def start_comment(model: str, sha: str, moved_from: Optional[str] = None) -> str:
    lines = [f"{MARK} **Projector review started** · model `{model}` · {effort_segment()}"
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
          configured_reviewer: Optional[str], model: str, loop: Optional[str],
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
                          "-f", f"body={start_comment(model, sha)}"], token)
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
        body = start_comment(state["model"], sha, moved_from=old)
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


# Publishing

PLACEHOLDERS = ("took", "seconds", "sha", "short_sha", "census")
PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
# Code the body quotes is left exactly as written: fenced blocks, then inline spans.
CODE = re.compile(r"^(```|~~~).*?^\1[^\n]*$|`[^`\n]+`", re.S | re.M)
# A comment of Projector's own at the start of a line, as a review or finding carries it.
OWN_MARKER = re.compile(r"^\s*<!--\s*projector-(review|finding)\b", re.M)
SIGNATURE = re.compile(
    r"^" + re.escape(MARK) + r" \*\*Projector review\*\* · model `[^`\n]+` · (?:effort `[^`\n]+` · )?"
    r"\*\*(APPROVED|CHANGES REQUESTED)\*\* · took (\d+m \d{2}s|\d+h \d{2}m)( over \d+ heads)?$"
)
MARKER = re.compile(
    r"^<!-- projector-review v=1 verdict=(approved|changes-requested) model=\S+ (?:effort=\S+ )?"
    r"sha=[0-9a-f]{40} findings=\d+ seconds=\d+ covered=\d+/\d+ -->$"
)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)
PRIORITY_HEADER = re.compile(r"^\*\*(P1|P2) · [^\n]+\*\*")
VERDICT_WORDS = {"approved": "APPROVED", "changes-requested": "CHANGES REQUESTED"}


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
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return max(0, int((at - start).total_seconds()))


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


def check_threads(threads: list[dict], ranges: dict[str, list[tuple[int, int]]]) -> None:
    """Refuse a finding GitHub could not anchor or a reader could not act on."""
    for i, t in enumerate(threads, 1):
        where = f"finding {i}"
        if not isinstance(t, dict):
            raise ReviewError(f"{where} is not an object with path, line, priority, and body")
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
            raise ReviewError(f"{where}: priority must be P1 or P2; a P3 goes in the body's Suggestions list")
        if OWN_MARKER.search(body):
            raise ReviewError(f"{where}: leave the finding marker out of the body; publish writes it with "
                              "the head's SHA")
        header = PRIORITY_HEADER.match(body)
        if not header or header.group(1) != priority:
            raise ReviewError(f"{where}: the body must open with `**{priority} · <what goes wrong>**`")
        if "**Fix:**" not in body:
            raise ReviewError(f"{where}: the body must carry a `**Fix:**` line")


def reviewer_verdicts(repo: str, number: int, reviewer: str, sha: str) -> list[int]:
    """The ids of every Projector verdict the reviewer posted on this exact SHA, from every page."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                   "--jq", f'.[] | select(.user.login == "{reviewer}") '
                           f'| select(.body | test("projector-review .* sha={sha}")) | .id']).split()
    return [int(r) for r in rows]


def read_record(root: Path, loop: str) -> list[dict]:
    record = loop_record(root, loop)
    return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else []


def check_collision(root: Path, state: dict, loop: Optional[str], second: Optional[int], body: str) -> None:
    """The collision check review-changes § Publish one review describes, read from the loop's record."""
    earlier = reviewer_verdicts(state["repo"], state["number"], state["reviewer"], state["sha"])
    if loop is not None:
        mine = {r["review_id"] for r in read_record(root, loop)
                if r.get("repo") == state["repo"] and r.get("number") == state["number"]}
        others = [i for i in earlier if i not in mine]
        if others:
            raise ReviewError(
                f"the reviewer already has a verdict on {state['sha'][:7]} that loop {loop} did not publish "
                f"(review {', '.join(map(str, others))}); another loop is reviewing this pull request, so ask "
                "the user which one continues")
        if any(i in mine for i in earlier) and not state.get("rereview"):
            raise ReviewError(
                f"loop {loop} already published a verdict on {state['sha'][:7]}; a re-review of this head "
                f"starts with `project review setup {state['number']} --rereview`")
        return
    if earlier:
        if second not in earlier:
            raise ReviewError(
                f"the reviewer already has a verdict on {state['sha'][:7]} (review {', '.join(map(str, earlier))}); "
                "ask the user, and if they want a second one pass --second-verdict with that review's id")
        if str(second) not in body:
            raise ReviewError(f"a second verdict must name the earlier review {second} in its body")


def self_review(repo: str, number: int, reviewer: str) -> bool:
    pr = gh_json(["api", f"repos/{repo}/pulls/{number}"])
    return ((pr or {}).get("user") or {}).get("login", "").lower() == reviewer.lower()


def compose(state: dict, verdict: str, body: str, covered: str, findings: int, census_line: str,
            at: datetime) -> str:
    """The review's text: the signature line and marker, then the body with its placeholders filled.

    The duration and `seconds=` come from one clock reading, so they cannot disagree."""
    if not body.strip():
        raise ReviewError("the body is empty; write the review below the signature line")
    if OWN_MARKER.search(body) or body.lstrip().startswith(MARK):
        raise ReviewError("leave the signature line and marker out of the body; publish writes them")
    if not PLACEHOLDER.search(CODE.sub("", body)) or "{census}" not in CODE.sub("", body):
        raise ReviewError("the body must print the census the verdict rests on; put {census} where it goes, "
                          "outside code")
    seconds = elapsed(state["start_comment"]["created_at"], at)
    took = duration(seconds, state["heads"])
    values = {"took": took, "seconds": str(seconds), "sha": state["sha"], "short_sha": state["sha"][:7],
              "census": census_line}
    # Placeholders are filled only in prose, so quoted code keeps its braces; anything else in
    # braces is the author's text, not a placeholder.
    pieces, last = [], 0
    for code in CODE.finditer(body):
        pieces.append(PLACEHOLDER.sub(lambda m: values[m.group(1)], body[last:code.start()]))
        pieces.append(code.group(0))
        last = code.end()
    pieces.append(PLACEHOLDER.sub(lambda m: values[m.group(1)], body[last:]))
    filled = "".join(pieces)
    effort = live_effort()
    signature = (f"{MARK} **Projector review** · model `{state['model']}` · {effort_segment()}"
                 f"**{VERDICT_WORDS[verdict]}** · took {took}")
    marker = (f"<!-- projector-review v=1 verdict={verdict} model={state['model']} "
              + (f"effort={effort} " if effort else "")
              + f"sha={state['sha']} findings={findings} seconds={seconds} covered={covered} -->")
    if not SIGNATURE.match(signature) or not MARKER.match(marker):
        raise ReviewError("the signature line or marker does not match review-changes/SKILL.md; check the "
                          f"model in the review's state ({state['model']})")
    return f"{signature}\n\n{marker}\n\n{filled.strip()}\n"


def publish(root: Path, number: int, repo: str, loop: Optional[str], verdict: str, body_path: Path,
            threads_path: Optional[Path], covered: str, second: Optional[int], allow_approve: bool) -> dict:
    """Submit the review, then set draft state, re-read, record, delete the start comment, and
    release the lock, in that order. Every refusal leaves the lock held for this review to retry."""
    paths = Paths(root, repo, number)
    state = read_state(paths)
    loop = loop if loop is not None else state.get("loop")
    repo, sha = state["repo"], state["sha"]
    if verdict not in VERDICT_WORDS:
        raise ReviewError(f"--verdict must be approved or changes-requested, not {verdict}")
    if not re.fullmatch(r"\d+/\d+", covered or ""):
        raise ReviewError("--covered must be <files read>/<files changed>, such as 12/12")
    if not paths.lock(sha).exists():
        raise ReviewError(f"no review of {repo}#{number} at {sha[:7]} holds the lock; "
                          f"run `project review setup {number}` again")
    published = state.get("published")
    if published and published.get("sha") == sha and state.get("start_comment"):
        # A publish that submitted and then stopped: finish it rather than post a second review.
        review_id, draft_wanted = published["id"], published["draft"]
    else:
        if not body_path.is_file():
            raise ReviewError(f"the body file {body_path} does not exist; compose the review there first")
        body = body_path.read_text(encoding="utf-8")
        threads = load_threads(threads_path)
        pr = pull_request(repo, number)
        if pr["head"]["sha"] != sha:
            raise ReviewError(f"the head moved to {pr['head']['sha'][:7]} since {sha[:7]} was set up; "
                              f"run `project review move {number}` and review the new head")
        check_collision(root, state, loop, second, body)
        check_threads(threads, diff_lines(repo, number) if threads else {})
        tally = census(repo, number)
        open_now = tally["open"] + len(threads)
        if verdict == "approved" and open_now:
            raise ReviewError(f"an approved verdict needs no open finding, and {tally['open']} are open and "
                              f"{len(threads)} are being posted; settle them or request changes")
        if verdict == "changes-requested" and not open_now:
            raise ReviewError("changes-requested needs an open finding or a new one; with none open the head is clean")
        total = tally["total"] + len(threads)
        noun = "thread" if total == 1 else "threads"
        census_line = f"{total} finding {noun}: {tally['resolved']} resolved, {open_now} open"
        text = compose(state, verdict, body, covered, len(threads), census_line, now())
        mine = self_review(repo, number, state["reviewer"])
        if mine or (verdict == "approved" and not allow_approve):
            event = "COMMENT"
        else:
            event = "APPROVE" if verdict == "approved" else "REQUEST_CHANGES"
        draft_wanted = (verdict == "changes-requested") if mine else None
        payload = {"commit_id": sha, "event": event, "body": text,
                   "comments": [{"path": t["path"], "line": t["line"], "side": "RIGHT",
                                 "body": f"<!-- projector-finding v=1 priority={t['priority']} sha={sha} -->\n"
                                         f"{t['body'].strip()}\n"} for t in threads]}
        token = reviewer_token(state["reviewer"])
        paths.folder.mkdir(parents=True, exist_ok=True)
        request = paths.folder / f"{number}-review.json"
        request.write_text(json.dumps(payload), encoding="utf-8")
        posted = gh_json(["api", "-X", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", str(request)],
                         token)
        request.unlink(missing_ok=True)
        review_id = posted["id"]
        # Recorded before anything else can fail, so a retry finishes this review rather than
        # posting a second one.
        state["published"] = {"id": review_id, "sha": sha, "verdict": verdict, "event": event,
                              "draft": draft_wanted}
        write_state(paths, state)
    token = reviewer_token(state["reviewer"])
    if draft_wanted is not None:
        run_gh(["pr", "ready", str(number), "--repo", repo, *(["--undo"] if draft_wanted else [])], token)
    posted = gh_json(["api", f"repos/{repo}/pulls/{number}/reviews/{review_id}"])
    if f"sha={sha}" not in (posted or {}).get("body", ""):
        raise ReviewError(f"review {review_id} does not carry sha={sha[:7]} when read back; check it on GitHub")
    if draft_wanted is not None:
        draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
        if draft != draft_wanted:
            raise ReviewError(f"{repo}#{number} is {'a draft' if draft else 'ready'} after the "
                              f"{state['published']['verdict']} verdict; set it with `gh pr ready` and retry")
    if loop is not None:
        record = read_record(root, loop)
        if not any(r.get("review_id") == review_id for r in record):
            record.append({"repo": repo, "number": number, "sha": sha, "review_id": review_id,
                           "verdict": state["published"]["verdict"], "published_at": now().isoformat()})
            loop_record(root, loop).parent.mkdir(parents=True, exist_ok=True)
            loop_record(root, loop).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    comment = (state.get("start_comment") or {}).get("id")
    if comment:
        try:
            run_gh(["api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment}"], token)
        except NotFound:
            pass
        state["start_comment"] = None
    write_state(paths, state)
    paths.lock(sha).unlink(missing_ok=True)
    return {"repo": repo, "number": number, "sha": sha, "review_id": review_id,
            "verdict": state["published"]["verdict"], "event": state["published"]["event"],
            "draft": draft_wanted}
