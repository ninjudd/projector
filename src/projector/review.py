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
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .core import ProjectorError, distribution_version
from .summary import repo_slug

FINDING_MARKER = "projector-finding"
# The mark that opens both the start comment and the review's signature line, as
# review-pr/SKILL.md § Label every review and finding writes it.
MARK = "📽️"
SHORT = 12
STALE_AFTER = timedelta(days=1)
# `review.peer_wait`'s default, and how often a wait reads GitHub again.
PEER_WAIT = 180
PEER_POLL = 15
sleep = time.sleep
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

    def worktree(self, sha: str) -> Path:
        return self.root / "worktrees" / self.repo / str(self.number) / self.key / sha[:SHORT]


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
                          f"{paths.model} shares its folder; finish that review and remove the folder first")
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


def start_comments(repo: str, number: int, reviewer: Optional[str] = None) -> list[dict]:
    """The start comments on the pull request, from every page, each with its `id`, `url`, `login`, the
    `sha` and `model` it names, and when it was created and last edited: the reviewer's alone when
    `reviewer` is given, and every account's otherwise."""
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
        login = (comment.get("login") or "").lower()
        if reviewer is not None and login != reviewer.lower() or not marker or not model:
            continue
        comments.append({"id": comment["id"], "url": comment.get("url"), "login": login, "sha": marker.group(1),
                         "model": model, "created_at": comment["created_at"],
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
                and (review.get("login") or "").lower() == comment["login"]
                and review.get("at") and parse_time(review["at"]) >= created):
            return False
    return True


# Resolving which review a command means

def valid_loop(loop: Optional[str]) -> Optional[str]:
    if loop is not None and not LOOP_ID.match(loop):
        raise ReviewError(f"--loop {loop!r} is not a usable id; use letters, digits, '.', '_' and '-'")
    return loop


def find_repo(root: Path, number: int, repo: Optional[str], cwd: Path) -> str:
    """The repository a command after setup means: --repo, else the one with a review of this pull
    request set up here, else the origin of the checkout the command runs in."""
    if repo:
        return repo
    states = (root / "prs").glob(f"*/*/{number}/*/state.json") if (root / "prs").is_dir() else []
    found = sorted({f"{p.parents[3].name}/{p.parents[2].name}" for p in states})
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
          rereview: bool) -> dict:
    checkout = checkout_root(checkout)
    origin = origin_repo(checkout)
    if repo and repo.lower() != origin.lower():
        raise ReviewError(f"--repo {repo} is not this checkout's origin {origin}; run from a checkout of {repo}")
    repo = origin
    paths = Paths(root, repo, number, model)
    recorded = ((stored_state(paths) or {}).get("start_comment") or {}).get("id")
    pr = pull_request(repo, number)
    sha = pr["head"]["sha"]
    lock = take_lock(paths, sha, loop)
    try:
        operator = authenticated_login()
        reviewer = reviewer_login(reviewer, configured_reviewer, operator)
        starts = start_comments(repo, number, reviewer)
        check_duplicate(starts, reviewer_reviews(repo, number, reviewer), model, recorded)
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
    if any(comment["id"] == recorded for comment in starts):
        # The review this machine set up before and never published is replaced by this one.
        delete_comment(repo, recorded, token)
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
        "identity": identity(model),
        "loop": loop,
        "rereview": rereview,
        "start_comment": {"id": posted["id"], "created_at": posted["created_at"]},
        "head_started_at": posted["created_at"],
        "heads": 1,
    }
    write_state(paths, state)
    return state


def check_duplicate(starts: list[dict], reviews: list[dict], model: str, recorded: Optional[int]) -> None:
    """Refuse a review while a live start comment shows the same model reviewing the pull request in
    another session: one this machine's state for the model does not record."""
    at = now()
    for comment in starts:
        if identity(comment["model"]) == identity(model) and comment["id"] != recorded \
                and is_live(comment, reviews, at):
            raise ReviewError(
                f"model {comment['model']} is already reviewing this pull request in another session: start comment "
                f"{comment['id']} ({comment.get('url') or 'no URL'}) names {comment['sha'][:7]}, posted "
                f"{comment['created_at']}; if that session has stopped, delete the comment and run setup again")


def delete_comment(repo: str, comment: int, token: str) -> None:
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
            raise ReviewError(f"the start comment {comment} still does not name {sha[:7]} after the edit; "
                              "edit it by hand")
    except BaseException:
        lock.unlink(missing_ok=True)
        raise
    paths.lock(old).unlink(missing_ok=True)
    state.update(sha=sha, base=merge_base, base_ref=pr["base"]["ref"], worktree=str(worktree),
                 trusted=trusted, untrusted_because=why or None,
                 cross_repository=((pr["head"].get("repo") or {}).get("full_name")) != state["repo"],
                 heads=state["heads"] + 1, worktrees=state["worktrees"] + [str(worktree)],
                 head_started_at=now().isoformat())
    write_state(paths, state)
    return state


THREADS_QUERY = """query($o:String!,$r:String!,$n:Int!,$after:String){ repository(owner:$o,name:$r){
  pullRequest(number:$n){ reviewThreads(first:100, after:$after){
    pageInfo{ hasNextPage endCursor }
    nodes{ id isResolved isOutdated path line originalLine
      comments(first:50){ nodes{ databaseId body createdAt author{ login } } } } } } } }"""


def finding_threads(repo: str, number: int) -> list[dict]:
    """Every Projector finding thread on the pull request, from any account, read to the last page,
    each with its comments, the finding first, and each comment's `id`, `body`, `login`, and `at`."""
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
                    "comments": [{"id": c.get("databaseId"), "body": c.get("body") or "",
                                  "login": ((c.get("author") or {}).get("login") or "").lower(),
                                  "at": c.get("createdAt")} for c in comments],
                })
        if not connection["pageInfo"]["hasNextPage"]:
            break
        after = connection["pageInfo"]["endCursor"]
    return threads


def census(repo: str, number: int, me: Optional[tuple[str, str]] = None, began: Optional[datetime] = None,
           wait: int = PEER_WAIT, settle: str = "") -> dict:
    """Every Projector finding thread on the pull request, with its replies and who re-checks it, so
    the review can verify and settle each one from this one list.

    `me` is the reviewer's login and the model running the review, and
    `began` when its review began on the head. Each thread gets the fields
    review-pr § Who re-checks a finding names: `model`, `verifier`, `mine`,
    `adopt`, `adopt_at`, and `answered`. `total`, `resolved`, and `open`
    count the threads marked `mine`, every thread when there is no `me`,
    and `others` counts the rest. With `settle`, it reads again every
    PEER_POLL seconds until no thread waits on its verifier's window: no
    answered thread with `answered`, as `census --wait` asks, and none at all
    with `all`, as `publish` asks, so a window that closes after the review
    cannot leave a finding no model re-checks."""
    while True:
        act = activity(repo, number, me)
        threads = [dict(t, **act.ownership(t, began or act.at, wait)) for t in act.threads]
        held = [parse_time(t["adopt_at"]) for t in threads
                if t["adopt"] == "wait" and (settle == "all" or t["answered"])]
        if not settle or not held:
            break
        sleep(max(1.0, min(PEER_POLL, (min(held) - now()).total_seconds())))
    counted = [t for t in threads if t["mine"] or me is None]
    others = [t for t in threads if not (t["mine"] or me is None)]
    resolved = sum(1 for t in counted if t["resolved"])
    return {"total": len(counted), "resolved": resolved, "open": len(counted) - resolved,
            "others": {"total": len(others), "open": sum(1 for t in others if not t["resolved"])},
            "model": me[1] if me else None, "threads": threads}


def census_line(result: dict, posting: int = 0) -> str:
    """The count a review body prints: this model's threads, with `posting` new ones among the open, and
    the other models' on a second clause when there are any."""
    total, open_now, others = result["total"] + posting, result["open"] + posting, result["others"]
    noun = "thread" if total == 1 else "threads"
    if not others["total"]:
        return f"{total} finding {noun}: {result['resolved']} resolved, {open_now} open"
    return (f"{total} finding {noun} from {result['model']}: {result['resolved']} resolved, {open_now} open. "
            f"Other models: {others['total']} thread{'' if others['total'] == 1 else 's'}, {others['open']} open")


def census_lines(result: dict) -> list[str]:
    lines = [census_line(result)]
    for t in result["threads"]:
        if not t["resolved"]:
            notes = [n for n in (t["model"], f"adopt {t['adopt']}" if t["adopt"] != "no" else None) if n]
            lines.append(f"{t['path']}:{t['line']}{' outdated' if t['outdated'] else ''}"
                         + (f" ({', '.join(notes)})" if notes else ""))
    return lines


# Who re-checks a finding

def activity(repo: str, number: int, me: Optional[tuple[str, str]] = None) -> "Activity":
    """Every model's posts on the pull request, read from GitHub now, as `me`, a login and a model, reads them."""
    pr = gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}
    return Activity(pr.get("head", {}).get("sha", ""), reviewer_reviews(repo, number), start_comments(repo, number),
                    finding_threads(repo, number), now(), (me[0].lower(), identity(me[1])) if me else None)


VERIFY_MARKER = re.compile(r"<!--\s*projector-verify\b([^>]*?)\s*-->")
FINDING_MODEL = re.compile(r"<!--\s*projector-finding\b[^>]*?\smodel=(\S+) -->")


def verify_reply(body: str) -> Optional[dict]:
    """A verify reply's marker, as `result`, `sha`, `model`, and `adopted-from`, or None for another comment."""
    marker = VERIFY_MARKER.search(body)
    return dict(re.findall(r"([a-z-]+)=(\S+)", marker.group(1))) if marker else None


def thread_model(thread: dict) -> Optional[str]:
    """The model a finding thread's marker names, or None for one an earlier release opened."""
    found = FINDING_MODEL.search(thread["comments"][0]["body"]) if thread["comments"] else None
    return found.group(1) if found else None


class Activity:
    """Every model's posts on the pull request, from every account, as review-pr § Who re-checks a
    finding reads them. A model is a login and an identity, since one holds within one account.

    `me` is the model running this review, which never reads itself as stopped."""

    def __init__(self, head: str, reviews: list[dict], starts: list[dict], threads: list[dict], at: datetime,
                 me: Optional[tuple[str, str]] = None) -> None:
        self.head, self.threads, self.at, self.me = head, threads, at, me
        self.verdicts: dict[tuple[str, str], list[dict]] = {}
        self.last: dict[tuple[str, str], datetime] = {}
        self.live: set[tuple[str, str]] = set()
        for review in reviews:
            marker = verdict_marker(review.get("body") or "")
            if marker is None or not review.get("at"):
                continue
            key = ((review.get("login") or "").lower(), identity(marker.model))
            self.verdicts.setdefault(key, []).append({"sha": marker.sha, "at": parse_time(review["at"]),
                                                      "verdict": marker.verdict})
            self.posted(key, parse_time(review["at"]))
        for comment in starts:
            key = (comment["login"], identity(comment["model"]))
            self.posted(key, parse_time(comment["updated_at"]))
            if is_live(comment, reviews, at):
                self.live.add(key)
        for thread in threads:
            for comment in thread["comments"][1:]:
                reply = verify_reply(comment["body"])
                if reply and reply.get("model") and comment.get("at"):
                    self.posted((comment["login"], identity(reply["model"])), parse_time(comment["at"]))

    def posted(self, key: tuple[str, str], at: datetime) -> None:
        if key not in self.last or at > self.last[key]:
            self.last[key] = at

    def opener(self, thread: dict) -> Optional[tuple[str, str]]:
        model = thread_model(thread)
        return (thread["comments"][0]["login"], identity(model)) if model else None

    def adoptions(self, thread: dict) -> list[dict]:
        """The thread's adoption replies in order, each with the `adopter`, the model it took the thread
        `from`, its time `at`, and its comment `id`."""
        holders, found = [self.opener(thread)], []
        for comment in thread["comments"][1:]:
            reply = verify_reply(comment["body"])
            if not (reply and reply.get("model") and reply.get("adopted-from") and comment.get("at")):
                continue
            source = identity(reply["adopted-from"])
            # The model it took over is the one of that identity that held the thread before it.
            taken = next((h for h in reversed(holders) if h and h[1] == source), (comment["login"], source))
            adopter = (comment["login"], identity(reply["model"]))
            found.append({"adopter": adopter, "from": taken, "at": parse_time(comment["at"]), "id": comment["id"]})
            holders.append(adopter)
        return found

    def rechecks(self, key: tuple[str, str]) -> list[dict]:
        """The threads `key` re-checks while it reviews: the ones it opened and the ones it adopted."""
        return [t for t in self.threads
                if self.opener(t) == key or any(a["adopter"] == key for a in self.adoptions(t))]

    def answers(self, thread: dict) -> list[datetime]:
        """When the author, or anyone but a review, replied on the thread."""
        return [parse_time(c["at"]) for c in thread["comments"][1:]
                if c.get("at") and not verify_reply(c["body"]) and FINDING_MARKER not in c["body"]]

    def owes(self, key: tuple[str, str], began: datetime) -> Optional[datetime]:
        """Since when `key` has owed the pull request a review and posted nothing, or None. It owes the
        head when it has a verdict on an earlier head and none on this one, a re-review when its newest
        verdict on the head requests changes and none of its threads is open, both since `began`, and
        an answer since the first reply on a thread it re-checks that is newer than all it posted."""
        if key in self.live:
            return None
        last, verdicts = self.last.get(key), self.verdicts.get(key, [])
        on_head = sorted((v for v in verdicts if v["sha"] == self.head), key=lambda v: v["at"])
        threads = self.rechecks(key)
        since = []
        if verdicts and not on_head:
            since.append(began)
        if on_head and on_head[-1]["verdict"] == "changes-requested" and all(t["resolved"] for t in threads):
            since.append(began)
        answers = [at for t in threads for at in self.answers(t) if last is None or at > last]
        if answers:
            since.append(min(answers))
        since = [at for at in since if last is None or last < at]
        return min(since) if since else None

    def recorded_stopped(self, key: tuple[str, str]) -> bool:
        """Whether an adoption reply naming `key` is newer than anything it has posted."""
        last = self.last.get(key)
        return any(a["from"] == key and (last is None or a["at"] > last)
                   for t in self.threads for a in self.adoptions(t))

    def stopped(self, key: tuple[str, str], began: datetime, wait: int) -> bool:
        if key == self.me:
            return False
        since = self.owes(key, began)
        return self.recorded_stopped(key) or since is not None and self.at >= since + timedelta(seconds=wait)

    def verifier(self, thread: dict, began: datetime, wait: int) -> Optional[tuple[str, str]]:
        """Who re-checks the thread now: the model that opened it unless it has stopped, else the first
        adopter that has not, else no one yet."""
        opener = self.opener(thread)
        if opener is None:
            return None
        for key in [opener] + [a["adopter"] for a in self.adoptions(thread)]:
            if not self.stopped(key, began, wait):
                return key
        return None

    def holder(self, thread: dict) -> tuple[str, str]:
        """The model that held the thread last: its newest adopter, else the model that opened it."""
        adoptions = self.adoptions(thread)
        return adoptions[-1]["adopter"] if adoptions else self.opener(thread)

    def ownership(self, thread: dict, began: datetime, wait: int) -> dict:
        """The census fields for the thread, as `me` reads them."""
        model = thread_model(thread)
        verifier = self.verifier(thread, began, wait)
        mine = model is None or (self.me is not None and verifier == self.me)
        adopt, adopt_at = "no", None
        if self.me is not None and model is not None and not mine:
            if verifier is None:
                adopt = "now"
            else:
                since = self.owes(verifier, began)
                if since is not None:
                    adopt, adopt_at = "wait", (since + timedelta(seconds=wait)).isoformat()
        checks = []
        for comment in thread["comments"][1:]:
            reply = verify_reply(comment["body"])
            if reply and comment.get("at") and \
                    (verifier is None or (comment["login"], identity(reply.get("model") or "")) == verifier):
                checks.append(parse_time(comment["at"]))
        answers = self.answers(thread)
        answered = bool(answers) and (not checks or max(answers) > max(checks))
        return {"model": model, "verifier": verifier[1] if verifier else None, "mine": mine, "adopt": adopt,
                "adopt_at": adopt_at, "answered": answered}


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


def producer(version: str, model: str, effort: Optional[str]) -> str:
    return f"Projector {version}, model {model}, " + (f"effort {effort}" if effort else "no effort")


def incremental_rules(root: Path, state: dict, loop: Optional[str], enabled: bool,
                      reviews: Optional[list[dict]] = None, wait: int = PEER_WAIT) -> dict:
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
        # A thread this model may adopt, resolved or not, counts as its own: a full review settles it.
        tally = census(repo, number, (state["reviewer"], state["model"]), parse_time(state["head_started_at"]), wait)
        open_findings = sum(1 for t in tally["threads"] if not t["resolved"] and t["mine"])
        adoptable = sum(1 for t in tally["threads"] if t["adopt"] != "no")
        if open_findings:
            raise Refused(f"{open_findings} finding thread{'' if open_findings == 1 else 's'} "
                          f"{'is' if open_findings == 1 else 'are'} open")
        if adoptable:
            raise Refused(f"{adoptable} finding thread{'' if adoptable == 1 else 's'} of a model that stopped "
                          "reviewing, or may stop, needs a full review to adopt it")
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
OWN_MARKER = re.compile(r"^\s*<!--\s*projector-(review|finding|incremental)\b", re.M)
SIGNATURE = re.compile(
    r"^" + re.escape(MARK) + r" \*\*Projector review\*\* · `[^`\n]+` · model `[^`\n]+` · "
    r"(?:effort `[^`\n]+` · )?\*\*(CLEAN|CHANGES REQUESTED)\*\* · (?:incremental from `[0-9a-f]{7}` · )?"
    r"took (\d+m \d{2}s|\d+h \d{2}m)( over \d+ heads)?$"
)
MARKER = re.compile(
    r"^<!-- projector-review v=1 verdict=(clean|changes-requested) projector=(\S+) model=(\S+) (?:effort=(\S+) )?"
    r"sha=([0-9a-f]{40}) findings=\d+ seconds=\d+ covered=\d+/\d+ -->$"
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
    incremental review, the head of the full review it builds on."""

    verdict: str
    sha: str
    projector: str
    model: str
    effort: Optional[str]
    incremental_from: Optional[str]


def verdict_marker(body: str) -> Optional[Verdict]:
    """The verdict a review body's marker line carries, or None when no line is a marker.

    An incremental review's line counts only directly after the marker, where
    publish writes it."""
    lines = [line.strip() for line in body.splitlines()]
    for i, line in enumerate(lines):
        marker = MARKER.match(line)
        if marker is None:
            continue
        verdict, projector, model, effort, sha = marker.groups()
        incremental = INCREMENTAL.match(lines[i + 1]) if i + 1 < len(lines) else None
        return Verdict(verdict, sha, projector, model, effort,
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


def reviewer_reviews(repo: str, number: int, reviewer: Optional[str] = None) -> list[dict]:
    """Every Projector review on the pull request, from every page, each with its `id`, `url`, `at`,
    `login`, and `body`: the reviewer's alone when `reviewer` is given, and every account's otherwise."""
    rows = run_gh(["api", "--paginate", f"repos/{repo}/pulls/{number}/reviews",
                   "--jq", '.[] | select(.body // "" | contains("projector-review")) '
                           '| {id, url: .html_url, at: .submitted_at, login: .user.login, body} | @json']).splitlines()
    reviews = [json.loads(row) for row in rows if row.strip()]
    return [r for r in reviews if reviewer is None or (r.get("login") or "").lower() == reviewer.lower()]


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


# Adopting a finding

ADOPT_RESULTS = ("fixed", "accepted", "withdrawn", "kept", "reopened")
THREAD_MUTATION = "mutation($id:ID!){ %s(input:{threadId:$id}){ thread{ isResolved } } }"


def adopt(paths: Paths, thread_id: str, result: str, body_path: Path, wait: int = PEER_WAIT) -> dict:
    """Take over the re-check of a finding whose model has stopped reviewing, by the steps review-pr
    § Who re-checks a finding gives.

    It refuses a thread that is this model's own or names no model, and one
    whose verifier still reviews: it owes no review, or has posted since it
    began to owe one, or another model that has not stopped adopted it first.
    While the verifier's window is open, it waits, reading again every
    PEER_POLL seconds. It then posts the adoption reply, reads the thread
    again, and applies `result` only when no earlier adoption by a model that
    has not stopped is there; otherwise it deletes its reply and refuses."""
    if result not in ADOPT_RESULTS:
        raise ReviewError(f"--result must be one of {', '.join(ADOPT_RESULTS)}, not {result}")
    state = read_state(paths)
    repo, number, reviewer, model = state["repo"], paths.number, state["reviewer"], state["model"]
    me, began = (reviewer.lower(), identity(model)), parse_time(state["head_started_at"])
    if not paths.lock(state["sha"]).exists():
        raise ReviewError(f"no review of {repo}#{number} at {state['sha'][:7]} by model {model} holds the lock; "
                          f"adopt within a review, after `project review setup {number}`")
    if not body_path.is_file():
        raise ReviewError(f"the body file {body_path} does not exist; write the reply there first")
    body = body_path.read_text(encoding="utf-8").strip()
    if not body:
        raise ReviewError("the body is empty; say what at this head settles the finding")
    if VERIFY_MARKER.search(body) or OWN_MARKER.search(body):
        raise ReviewError("leave the marker out of the body; adopt writes it")

    def thread_now(act: Activity) -> dict:
        if act.head != state["sha"]:
            raise ReviewError(f"the head moved to {act.head[:7]} since {state['sha'][:7]} was set up; run "
                              f"`project review move {number}` and re-check the thread against the new head")
        found = next((t for t in act.threads if t["id"] == thread_id), None)
        if found is None:
            raise ReviewError(f"{repo}#{number} has no Projector finding thread {thread_id}")
        return found

    while True:
        act = activity(repo, number, (reviewer, model))
        thread = thread_now(act)
        if act.opener(thread) is None:
            raise ReviewError(f"thread {thread_id} names no model, so it is every model's; settle it as your own")
        verifier = act.verifier(thread, began, wait)
        if verifier == me:
            raise ReviewError(f"thread {thread_id} is yours to re-check; settle it as your own")
        if verifier is None:
            source = act.holder(thread)
            break
        since = act.owes(verifier, began)
        if since is None:
            raise ReviewError(f"{verifier[1]} still reviews this pull request and re-checks thread {thread_id}; "
                              "leave it to that model")
        sleep(max(1.0, min(PEER_POLL, (since + timedelta(seconds=wait) - now()).total_seconds())))
    token = reviewer_token(reviewer)
    text = (f"<!-- projector-verify v=1 result={result} sha={state['sha']} model={model} adopted-from={source[1]} -->\n"
            f"Re-checked by `{model}` for `{source[1]}`, which has stopped reviewing this pull request.\n\n{body}\n")
    reply = gh_json(["api", f"repos/{repo}/pulls/{number}/comments/{thread['comments'][0]['id']}/replies",
                     "-f", f"body={text}"], token)
    act = activity(repo, number, (reviewer, model))
    thread = thread_now(act)
    adoptions = act.adoptions(thread)
    earlier = adoptions[:next((i for i, a in enumerate(adoptions) if a["id"] == reply["id"]), len(adoptions))]
    first = next((a for a in earlier if a["adopter"] != me and not act.stopped(a["adopter"], began, wait)), None)
    if first is not None:
        try:
            run_gh(["api", "-X", "DELETE", f"repos/{repo}/pulls/comments/{reply['id']}"], token)
        except NotFound:
            pass
        raise ReviewError(f"{first['adopter'][1]} adopted thread {thread_id} first and re-checks it; your reply "
                          "is deleted, so leave the thread to that model")
    change = {"fixed": "resolveReviewThread", "accepted": "resolveReviewThread",
              "withdrawn": "resolveReviewThread", "reopened": "unresolveReviewThread"}.get(result)
    if change and thread["resolved"] != (change == "resolveReviewThread"):
        run_gh(["api", "graphql", "-f", f"query={THREAD_MUTATION % change}", "-f", f"id={thread_id}"], token)
    return {"repo": repo, "number": number, "thread": thread_id, "result": result, "reply": reply["id"],
            "adopted_from": source[1], "resolved": thread["resolved"] if change is None
            else change == "resolveReviewThread"}


# The sign-off

def models_on(head: str, reviews: list[dict], starts: list[dict], at: datetime) -> dict[str, dict]:
    """Each model with a Projector verdict or a start comment among the reviewer's, keyed by identity.

    `verdict` is its newest verdict on `head`, and `earlier` its newest on
    another head, each with the review's `id`, `url`, `at`, `sha`, and
    `verdict`, or None. `reviewing` is its live start comment on `head`, and
    `elsewhere` one on another head, each None when there is none."""
    models: dict[str, dict] = {}

    def entry(model: str) -> dict:
        return models.setdefault(identity(model), {"model": identity(model), "verdict": None, "earlier": None,
                                                   "reviewing": None, "elsewhere": None})

    for review in reviews:
        marker = verdict_marker(review.get("body") or "")
        if marker is None or not review.get("at"):
            continue
        found = entry(marker.model)
        key = "verdict" if marker.sha == head else "earlier"
        if found[key] is None or parse_time(review["at"]) >= parse_time(found[key]["at"]):
            found[key] = {"id": review.get("id"), "url": review.get("url"), "at": review["at"], "sha": marker.sha,
                          "verdict": marker.verdict}
    for comment in starts:
        if is_live(comment, reviews, at):
            entry(comment["model"])["reviewing" if comment["sha"] == head else "elsewhere"] = comment
    return models


def signoff(models: dict[str, dict], open_threads: int, stopped: frozenset = frozenset()) -> str:
    """The rule review-pr § Review as one model among several states: `changes-requested` when any
    model's newest verdict on the head requests changes, `waiting` while a model is reviewing the head
    or a Projector finding thread is open, `clean` when every model's newest verdict on it is clean,
    and `unreviewed` when no model has one. The verdicts of a model recorded as `stopped` do not
    count, because the model that adopted its findings speaks for them."""
    verdicts = [m["verdict"]["verdict"] for key, m in models.items() if m["verdict"] and key not in stopped]
    if "changes-requested" in verdicts:
        return "changes-requested"
    if open_threads or any(m["reviewing"] for m in models.values()):
        return "waiting"
    return "clean" if verdicts else "unreviewed"


def pending_peers(models: dict[str, dict], me: str, stopped: frozenset = frozenset()) -> list[str]:
    """The other models, not recorded as stopped, that reviewed an earlier head, or are reviewing another
    one, and have neither a verdict nor a live start comment on this one: a loop that has not noticed
    the push yet."""
    return sorted(key for key, m in models.items() if key != me and key not in stopped
                  and (m["earlier"] or m["elsewhere"]) and not (m["verdict"] or m["reviewing"]))


def stopped_models(act: Activity, reviewer: str, models: dict[str, dict]) -> dict[str, str]:
    """The reviewer's models recorded as stopped, each with the model whose adoption reply recorded it."""
    stopped = {}
    for key in models:
        model = (reviewer.lower(), key)
        if act.recorded_stopped(model):
            newest = max((a for t in act.threads for a in act.adoptions(t) if a["from"] == model),
                         key=lambda a: a["at"])
            stopped[key] = newest["adopter"][1]
    return stopped


def peer_wait(value: object) -> int:
    """`review.peer_wait`: unset is the default, and anything but a non-negative integer is refused."""
    if value is None:
        return PEER_WAIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReviewError(f"review.peer_wait must be a non-negative number of seconds, not {value!r}")
    return value


def sign(repo: str, number: int, reviewer: str, head: str, me: str, deadline: datetime,
         posted: Optional[dict] = None) -> dict:
    """Apply the sign-off rule to the head this review published on, waiting for pending peers when it
    says `clean`.

    The rule reads the reviewer's verdicts and start comments, and every
    finding thread; `posted` stands in for this review's verdict before it is
    submitted. While the rule says
    `clean` and a peer is pending, it re-reads both every PEER_POLL seconds
    until `deadline`: a peer's start comment makes it `waiting`, a peer's
    verdict joins the rule, and a peer that posts nothing by the deadline is
    absent. A head that moves ends the wait, because the reviews of the new
    head decide it. Returns `signoff`, the `models` it read, how many whole
    seconds it `waited`, the peers still `pending`, and whether the head
    `moved`."""
    started = now()
    threads = finding_threads(repo, number)
    open_threads = sum(1 for t in threads if not t["resolved"])
    while True:
        reviews = [r for r in reviewer_reviews(repo, number, reviewer) if not posted or r.get("id") != posted["id"]] \
            + ([posted] if posted else [])
        starts, at = start_comments(repo, number, reviewer), now()
        models = models_on(head, reviews, starts, at)
        stopped = frozenset(stopped_models(Activity(head, reviews, starts, threads, at), reviewer, models))
        result = signoff(models, open_threads, stopped)
        pending = pending_peers(models, me, stopped) if result == "clean" else []
        waited = int((now() - started).total_seconds())
        if not pending or now() >= deadline:
            return {"signoff": result, "models": models, "waited": waited, "pending": pending, "moved": False}
        sleep(max(0.0, min(PEER_POLL, (deadline - now()).total_seconds())))
        if pull_request(repo, number)["head"]["sha"] != head:
            return {"signoff": result, "models": models, "waited": int((now() - started).total_seconds()),
                    "pending": pending, "moved": True}


def status(repo: str, number: int, reviewer: str, wait: int = PEER_WAIT) -> dict:
    """The sign-off of the pull request's current head, by the rule `publish` applies, with each model's
    part in it and every open Projector finding thread with who re-checks it."""
    try:
        pr = gh_json(["api", f"repos/{repo}/pulls/{number}"])
    except NotFound as exc:
        raise ReviewError(f"{repo}#{number} does not exist or is not visible to this account") from exc
    head, at = pr["head"]["sha"], now()
    reviews, starts = reviewer_reviews(repo, number), start_comments(repo, number)
    act = Activity(head, reviews, starts, finding_threads(repo, number), at)
    own_reviews = [r for r in reviews if (r.get("login") or "").lower() == reviewer.lower()]
    own_starts = [c for c in starts if c["login"] == reviewer.lower()]
    models = models_on(head, own_reviews, own_starts, at)
    stopped = stopped_models(act, reviewer, models)
    open_threads = [t for t in act.threads if not t["resolved"]]
    rows = []
    for key in sorted(models):
        m = models[key]
        state = "reviewing" if m["reviewing"] else "stopped" if key in stopped \
            else m["verdict"]["verdict"] if m["verdict"] else "pending"
        row = {"model": m["model"], "state": state}
        if key in stopped:
            row["adopted_by"] = stopped[key]
        verdict = m["verdict"] or m["earlier"]
        if verdict:
            row.update(review=verdict["id"], url=verdict["url"], at=verdict["at"], sha=verdict["sha"])
        comment = m["reviewing"] or m["elsewhere"]
        if comment:
            row.update(comment=comment["id"], since=comment["updated_at"], comment_sha=comment["sha"])
        rows.append(row)
    return {"repo": repo, "number": number, "head": head, "draft": bool(pr.get("draft")),
            "signoff": signoff(models, len(open_threads), frozenset(stopped)), "models": rows,
            "open_threads": [{"path": t["path"], "line": t["line"], "model": thread_model(t),
                              "verifier": act.ownership(t, at, wait)["verifier"]} for t in open_threads]}


def status_lines(result: dict) -> list[str]:
    """`review status` without --json: the sign-off, a line for each model, and the open threads."""
    lines = [f"#{result['number']} at {result['head'][:7]}: {result['signoff']}"]
    width = max((len(row["model"]) for row in result["models"]), default=0) + 3
    for row in result["models"]:
        if row["state"] == "reviewing" or row["state"] == "pending" and "comment" in row:
            since = parse_time(row["since"]).strftime("%H:%M UTC")
            where = "" if row["state"] == "reviewing" else f" on {row['comment_sha'][:7]}"
            detail = f"start comment {row['comment']}{where}, since {since}"
        elif row["state"] == "stopped":
            detail = f"findings adopted by {row['adopted_by']}"
        else:
            detail = f"review {row['review']}" + (f" on {row['sha'][:7]}" if row["sha"] != result["head"] else "")
        lines.append(f"  {row['model']:<{width}}{row['state']:<20}{detail}")
    threads = result["open_threads"]
    if threads:
        named = ", ".join(f"{t['path']}:{t['line']}" + (f" ({t['model']})" if t["model"] else "")
                          + (f", re-checked by {t['verifier']}" if t["verifier"] and t["verifier"] != t["model"]
                             else "") for t in threads)
        lines.append(f"  {len(threads)} open finding thread{'' if len(threads) == 1 else 's'}: {named}")
    return lines


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
    signature = (f"{MARK} **Projector review** · {producer_segments(version, state['model'], effort)}"
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
            at: datetime) -> str:
    """The review's text: the signature line and marker, then the body with its placeholders filled."""
    check_body(body)
    if "{census}" not in prose(body):
        raise ReviewError("the body must print the census the verdict rests on; put {census} where it goes, "
                          "outside code")
    signature, marker, values = heading(state, verdict, findings, covered, at)
    filled = fill(body, dict(values, census=census_line))
    return f"{signature}\n\n{marker}\n\n{filled.strip()}\n"


def listed(items: list[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


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
            incremental: bool = False, enabled: bool = True, wait: int = PEER_WAIT) -> dict:
    """Submit the review, then apply the sign-off rule and set draft state, re-read, record, delete the
    start comment, and release the lock, in that order. Every refusal leaves the lock held for this
    review to retry.

    With `incremental`, the review is a clean `COMMENT` that builds on this loop's last full
    review, published only while `incremental_rules` passes, with `enabled` from
    `review.incremental`. `wait` is `review.peer_wait`: how long after this review began on the
    head the rule waits for a model that reviewed an earlier head before it signs off."""
    root, number = paths.root, paths.number
    state = read_state(paths)
    loop = loop if loop is not None else state.get("loop")
    repo, sha, reviewer = state["repo"], state["sha"], state["reviewer"]
    me = identity(state["model"])
    began = parse_time(state["head_started_at"])
    deadline = began + timedelta(seconds=wait)
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
        pr = pull_request(repo, number)
        if pr["head"]["sha"] != sha:
            raise ReviewError(f"the head moved to {pr['head']['sha'][:7]} since {sha[:7]} was set up; "
                              f"run `project review move {number}` and review the new head")
        reviews = reviewer_reviews(repo, number, reviewer)
        check_collision(root, state, loop, second, body, reviews)
        incremental_from = None
        mine = ((pr.get("user") or {}).get("login") or "").lower() == reviewer.lower()
        if incremental:
            rules = incremental_rules(root, state, loop, enabled, reviews, wait)
            if not rules["incremental"]:
                raise ReviewError(f"an incremental review is refused: {rules['reason']}; run the full review "
                                  "on this setup")
            incremental_from = rules["from"]
            text = compose_incremental(state, body, rules, not mine, now())
            event = "COMMENT"
        else:
            check_threads(threads, diff_lines(repo, number) if threads else {})
            # A thread whose verifier's window is open waits until it closes: published before then, the
            # review would leave a finding that no model re-checks once the window closed.
            tally = census(repo, number, (reviewer, state["model"]), began, wait, settle="all")
            adoptable = [t for t in tally["threads"] if t["adopt"] == "now"]
            if adoptable:
                listed_threads = ", ".join(f"{t['path']}:{t['line']} ({t['model']}, thread {t['id']}"
                                           + (", resolved)" if t["resolved"] else ")") for t in adoptable)
                raise ReviewError(f"{len(adoptable)} finding thread{'' if len(adoptable) == 1 else 's'} of a "
                                  f"model that stopped reviewing {'is' if len(adoptable) == 1 else 'are'} yours to "
                                  f"re-check: {listed_threads}; settle each with `project review adopt`, a resolved "
                                  "one whose fix holds with --result fixed")
            open_after = tally["open"] + len(threads)
            if verdict == "clean" and open_after:
                raise ReviewError(f"a clean verdict needs no open finding of this model's, and {tally['open']} are "
                                  f"open and {len(threads)} are being posted; settle them or request changes")
            if verdict == "changes-requested" and not open_after:
                raise ReviewError("changes-requested needs an open finding of this model's or a new one; with none "
                                  "open the head is clean")
            text = compose(state, verdict, body, covered, len(threads), census_line(tally, len(threads)), now())
            if mine or verdict == "clean" and not allow_approve:
                event = "COMMENT"
            elif verdict == "changes-requested":
                event = "REQUEST_CHANGES"
            else:
                # One account's newest approval or change request sets reviewDecision, so an approval would
                # replace another model's standing change request: approve only when the rule, counting
                # this verdict as posted, signs the head off.
                posted = {"id": None, "url": None, "at": now().isoformat(), "login": reviewer, "body": text}
                ahead = sign(repo, number, reviewer, sha, me, deadline, posted)
                if ahead["moved"]:
                    raise ReviewError(f"the head moved since {sha[:7]} was set up, while publish waited for the "
                                      f"other models; run `project review move {number}` and review the new head")
                event = "APPROVE" if ahead["signoff"] == "clean" else "COMMENT"
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
    # The rule counts this review as read back, whether or not the reviews listing shows it yet.
    posted = {"id": review_id, "url": read.get("html_url"), "at": read.get("submitted_at") or now().isoformat(),
              "login": reviewer, "body": read["body"]}
    draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
    # Only a draft is about to be signed off, so only a draft waits for its peers.
    signed = sign(repo, number, reviewer, sha, me, deadline if published.get("self") and draft else now(), posted)
    draft_wanted = None
    if published.get("self") and not signed["moved"]:
        draft_wanted = {"changes-requested": True, "clean": False}.get(signed["signoff"])
    if draft_wanted is not None and draft_wanted != draft:
        run_gh(["pr", "ready", str(number), "--repo", repo, *(["--undo"] if draft_wanted else [])], token)
    if draft_wanted is not None:
        draft = bool((gh_json(["api", f"repos/{repo}/pulls/{number}"]) or {}).get("draft"))
        if draft != draft_wanted:
            raise ReviewError(f"{repo}#{number} is {'a draft' if draft else 'ready'} after the sign-off "
                              f"{signed['signoff']}; set it with `gh pr ready` and retry")
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
            "event": published["event"], "draft": draft_wanted, "from": published.get("from"),
            "signoff": signed["signoff"], "waited": signed["waited"], "pending": signed["pending"],
            "moved": signed["moved"]}
