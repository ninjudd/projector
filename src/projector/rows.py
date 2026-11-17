"""A session's pull request rows, as Projector's panel lists them, read from the files the panel reads.

The panel's row model lives in TypeScript, in `hooks/github.ts`, `hooks/parse.ts`, and `hooks/register.tsx`.
This module ports it for `project console`, keeping the panel's names. Both copies run the cases in
`tests/fixtures/rows/`, through `tests/test_rows.py` and `hooks/rows.test.ts`, so a rule that changes in one
copy only fails the other copy's tests.

The files, under the watch daemon's state root:

    sessions/<session>/subscriptions            the session's subscription lines
    sessions/<session>/state.json               `adopted`: the pull requests its repository lines cover
    daemon.json                                 `operator`: the login the rows are seen as
    cache/<owner>/<repo>/pulls/<number>.json    the daemon's last result for a pull request
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from . import review, summary, watch
from .inbox import read_json

# The roles in the order a drawer names them, and the role each subscription role holds.
ROLES = ("fixing", "reviewing")
ROLE_OF = {"fix": "fixing", "review": "reviewing"}

SECTIONS = ("pr", "review")

CHECKS = {"SUCCESS": "passing", "FAILURE": "failing", "ERROR": "failing", "PENDING": "pending", "EXPECTED": "pending"}

# What each check run's and commit status's state counts as. A state missing here, such as `SKIPPED`, counts
# as passing.
CONTEXT_CHECKS = {
    "checkRunCountsByState": {
        **dict.fromkeys(watch.CHECK_FAILURES, "failing"),
        "CANCELLED": "cancelled",
        **dict.fromkeys(("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"), "pending"),
    },
    "statusContextCountsByState": {**dict.fromkeys(watch.STATUS_FAILURES, "failing"),
                                   **dict.fromkeys(("PENDING", "EXPECTED"), "pending")},
}

SUMMARY = re.compile(r"^<!-- " + summary.SUMMARY_MARKER + r" v=1 sha=[0-9a-f]{40} -->\r?\n"
                     r"\S* ?\*\*Projector summary\*\* [^\r\n]*?(https?://\S+)\s*$", re.M)

# GitHub counts `comments` under `reviewThreads(first: 100)` as 100 requests, so only an open drawer asks
# for these, and only for its own pull request.
THREAD_FIELDS = ("reviewThreads(first: 100) { nodes { isResolved isOutdated path line originalLine\n"
                 "  comments(first: 1) { nodes { url bodyText author { login } } } } }")

# The panel sorts with JavaScript's `localeCompare`, which orders by Unicode's root collation rather than by
# code point: `_` before `-`, both before `.` and `/`, and punctuation before digits. These are the characters
# of a lowercased repository name or login.
COLLATION = {character: rank for rank, character in enumerate("_-./0123456789abcdefghijklmnopqrstuvwxyz")}


@dataclass(frozen=True)
class Pull:
    """A pull request as a row draws it: the panel's `PullRequest`, less the label colors."""

    repo: str
    number: int
    title: str
    url: str
    state: str
    is_draft: bool
    review_decision: Optional[str]
    is_conflicting: bool
    open_threads: int
    author: Optional[str]
    branch: str
    additions: float
    deletions: float
    changed_files: float
    checks: Optional[str]
    is_cancelled: bool
    labels: list[str]
    summary_url: Optional[str]
    is_loading: bool = False


@dataclass(frozen=True)
class Row:
    """One line of the pull request list: its section, its label, the word after the label, the notes after
    the title, and the roles the session holds on it."""

    section: str
    label: str
    state: str
    notes: list[str]
    roles: list[str]
    pull: Pull


@dataclass(frozen=True)
class Thread:
    """An unresolved review thread, as a pull request's drawer lists it."""

    path: str
    line: Optional[int]
    is_outdated: bool
    author: Optional[str]
    excerpt: str
    url: Optional[str]


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def number_of(value: object) -> float:
    return value if is_number(value) else 0


def string_of(value: object) -> Optional[str]:
    return value if isinstance(value, str) else None


def record_of(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def nodes_of(value: object) -> list:
    nodes = record_of(value).get("nodes")
    return nodes if isinstance(nodes, list) else []


def first_line(text: str) -> str:
    return text.strip().split("\n")[0]


# Subscriptions

def subscribed_of(lines: list[watch.Subscription], adopted: list[tuple[str, int]]
                  ) -> list[tuple[tuple[str, int], list[str]]]:
    """The pull requests a session subscribes to, in the order its lines name them, then those its repository
    lines adopted, each once with its subscription roles. A `!` line takes the pull request out of that role,
    and for `review` out of that model, wherever the line sits. An adopted pull request stays only while a
    repository line reviews it with a model no `!` line names."""
    excluded = {(line.repo.lower(), line.number, line.role_name) for line in lines
                if line.excluded and line.number is not None}
    live = [line for line in lines if not line.excluded]
    found: dict[tuple[str, int], list[str]] = {}

    def add(pull: tuple[str, int], role: str) -> None:
        roles = found.setdefault(pull, [])
        if role not in roles:
            roles.append(role)

    for line in live:
        if line.number is not None and (line.repo.lower(), line.number, line.role_name) not in excluded:
            add((line.repo.lower(), line.number), line.role)
    repositories = [line for line in live if line.number is None]
    for repo, number in adopted:
        if any(line.repo.lower() == repo and (repo, number, line.role_name) not in excluded for line in repositories):
            add((repo, number), "review")
    return list(found.items())


def adopted_of(state: Optional[dict]) -> list[tuple[str, int]]:
    """The pull requests the daemon adopted for the session's repository lines, from its `state.json`:
    `{"adopted": {"owner/repo": [12, 14]}}`."""
    adopted = record_of(record_of(state).get("adopted"))
    return [(repo.lower(), int(number)) for repo, numbers in adopted.items()
            if watch.REPO_ONLY.match(repo) and isinstance(numbers, list)
            for number in numbers if is_number(number) and float(number).is_integer() and number > 0]


def operator_of(daemon: Optional[dict]) -> Optional[str]:
    """The login `daemon.json` names as the operator, or None when it names none."""
    operator = record_of(daemon).get("operator")
    return operator if isinstance(operator, str) and operator else None


# Pull requests

def suite_of(context: dict) -> dict:
    return record_of(context.get("checkSuite")) if context.get("__typename") == "CheckRun" else {}


def newest_runs(contexts: list[dict]) -> list[Optional[str]]:
    """The conclusion of each workflow's newest run on the head, among the check runs the rollup lists."""
    newest: dict[int, tuple[str, Optional[str]]] = {}
    for context in contexts:
        run = watch.workflow_run(context) if context.get("__typename") == "CheckRun" else None
        if run is None:
            continue
        workflow, at = run
        if workflow not in newest or at > newest[workflow][0]:
            newest[workflow] = (at, string_of(suite_of(context).get("conclusion")))
    return [conclusion for _, conclusion in newest.values()]


def context_nodes(commit: dict) -> list[dict]:
    rollup = record_of(commit.get("statusCheckRollup"))
    return [context for context in nodes_of(rollup.get("contexts")) if isinstance(context, dict)]


def checks_of(commit: dict) -> Optional[str]:
    """The head's checks from the rollup's counts, which cover every check run and commit status, less the
    `projector/review` status a review posts and each failed check run of a cancelled workflow run that the
    cancel caused or that a newer run tests again. A head with nothing failing or pending reads `cancelled`
    when every workflow's newest run on it was cancelled. Without the counts the rollup's own state stands."""
    rollup = record_of(commit.get("statusCheckRollup"))
    state = CHECKS.get(rollup["state"]) if isinstance(rollup.get("state"), str) else None
    contexts = rollup.get("contexts")
    if not isinstance(contexts, dict) or not any(isinstance(contexts.get(key), list) for key in CONTEXT_CHECKS):
        return state

    counts = dict.fromkeys(("failing", "pending", "passing", "cancelled"), 0)
    for key, states in CONTEXT_CHECKS.items():
        for row in contexts.get(key) if isinstance(contexts.get(key), list) else []:
            if isinstance(row, dict) and isinstance(row.get("state"), str) and is_number(row.get("count")):
                counts[states.get(row["state"], "passing")] += row["count"]
    own = string_of(record_of(record_of(commit.get("status")).get("context")).get("state"))
    if own is not None:
        counts[CONTEXT_CHECKS["statusContextCountsByState"].get(own, "passing")] -= 1
    nodes = context_nodes(commit)
    times = watch.cancel_times(nodes)
    for context in nodes:
        if context.get("conclusion") in watch.CHECK_FAILURES and (
                watch.caused_by_cancel(context, times) or watch.superseded(context, nodes)):
            counts["failing"] -= 1
            counts["cancelled"] += 1
    if counts["failing"] > 0:
        return "failing"
    if counts["pending"] > 0:
        return "pending"
    newest = newest_runs(nodes)
    if newest and all(conclusion == "CANCELLED" for conclusion in newest):
        return "cancelled"
    return next((checks for checks in ("passing", "cancelled") if counts[checks] > 0), None)


def cancelled_of(commit: dict) -> bool:
    """Whether a workflow's newest run on the head was cancelled, so nothing has run its checks since."""
    return "CANCELLED" in newest_runs(context_nodes(commit))


def to_pull(repo: str, number: int, node: dict) -> Pull:
    """A pull request from the node the daemon's query returned for it, as the panel's `toPullRequest` reads
    it: a field of the wrong type reads as missing."""
    commits = nodes_of(node.get("commits"))
    head = record_of(record_of(commits[0]).get("commit")) if commits else {}
    author = record_of(node.get("author")).get("login")
    url = node.get("url")
    body = node.get("body")
    found = SUMMARY.search(body) if isinstance(body, str) else None
    return Pull(
        repo=repo,
        number=number,
        title=string_of(node.get("title")) or "",
        url=url if isinstance(url, str) else f"https://github.com/{repo}/pull/{number}",
        state=node["state"] if node.get("state") in ("MERGED", "CLOSED") else "OPEN",
        is_draft=node.get("isDraft") is True,
        review_decision=node["reviewDecision"] if node.get("reviewDecision") in (
            "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED") else None,
        is_conflicting=node.get("mergeable") == "CONFLICTING",
        open_threads=sum(1 for thread in nodes_of(node.get("reviewThreads"))
                         if isinstance(thread, dict) and thread.get("isResolved") is False),
        author=author if isinstance(author, str) else None,
        branch=string_of(node.get("headRefName")) or "",
        additions=number_of(node.get("additions")),
        deletions=number_of(node.get("deletions")),
        changed_files=number_of(node.get("changedFiles")),
        checks=checks_of(head),
        is_cancelled=cancelled_of(head),
        labels=[label["name"] for label in nodes_of(node.get("labels"))
                if isinstance(label, dict) and isinstance(label.get("name"), str)],
        summary_url=found.group(1) if found else None,
    )


def cached_pull(root: Path, repo: str, number: int) -> Optional[Pull]:
    """The daemon's last result for a pull request, `{"repository": {"pullRequest": {...}}, ...}`, or None
    when it has none yet or the file holds no pull request."""
    node = record_of(record_of(read_json(watch.pull_cache(root, repo, number))).get("repository")).get("pullRequest")
    return to_pull(repo, number, node) if isinstance(node, dict) else None


def loading_pull(repo: str, number: int) -> Pull:
    """A pull request the session subscribes to that the daemon has no result for yet."""
    return replace(to_pull(repo, number, {}), is_loading=True)


# The row model

HOLDS: tuple[tuple[str, Callable[[Pull], bool]], ...] = (
    ("conflict", lambda pull: pull.is_conflicting),
    ("failing", lambda pull: pull.checks == "failing"),
    ("changes", lambda pull: pull.review_decision == "CHANGES_REQUESTED"),
    ("findings", lambda pull: pull.open_threads > 0),
)

# The labels a review sets, in the order a row's word takes them.
REVIEW_LABELS = (review.REVIEWING_LABEL, review.CLEAN_LABEL)


def has_label(pull: Pull, name: str) -> bool:
    return any(label.lower() == name for label in pull.labels)


def row_state(pull: Pull) -> str:
    """The word after a row's label: the first of `HOLDS` that holds, then its review label, and otherwise
    `draft` or `ready`."""
    hold = next((status for status, holds in HOLDS if holds(pull)), None)
    label = next((name for name in REVIEW_LABELS if has_label(pull, name)), None)
    return hold or label or ("draft" if pull.is_draft else "ready")


def notes_of(pull: Pull) -> list[str]:
    """What a row says after the title: every other status that applies, in the order the word takes them,
    then `draft` when another status takes the word, its approval, `passing` while the head's checks pass,
    and `cancelled` while a workflow's newest run on the head was cancelled."""
    shown = row_state(pull)
    notes = [status for status, holds in HOLDS if holds(pull) and status != shown]
    notes += [name for name in REVIEW_LABELS if name != shown and has_label(pull, name)]
    if pull.is_draft and shown != "draft":
        notes.append("draft")
    if pull.review_decision == "APPROVED":
        notes.append("approved")
    if pull.checks == "passing":
        notes.append("passing")
    if pull.is_cancelled:
        notes.append("cancelled")
    return notes


def is_review_request(pull: Pull, viewer: Optional[str]) -> bool:
    """Whether someone other than the viewer wrote the pull request. Until the viewer is known, and while a
    pull request loads, it lists with the viewer's own."""
    return viewer is not None and not pull.is_loading and pull.author != viewer


def collated(text: str) -> tuple[int, ...]:
    return tuple(COLLATION.get(character, len(COLLATION) + ord(character)) for character in text)


def newest_first(pull: Pull) -> tuple:
    return collated(pull.repo), -pull.number


def by_section(pulls: list[Pull], viewer: Optional[str]) -> dict[str, list[Pull]]:
    """The pull requests in the order the panel lists them, by section: yours, then the review requests, each
    author's together and the authors by login. Within each, by repository and newest first."""
    return {
        "pr": sorted((pull for pull in pulls if not is_review_request(pull, viewer)), key=newest_first),
        "review": sorted((pull for pull in pulls if is_review_request(pull, viewer)),
                         key=lambda pull: (collated((pull.author or "").lower()), *newest_first(pull))),
    }


def labeler(pulls: list[Pull]) -> Callable[[Pull], str]:
    """Labels a pull request `#12` when the rows hold one repository, `name#12` when the repositories' names
    tell them apart, and `owner/repo#12` otherwise."""
    repos = list(dict.fromkeys(pull.repo for pull in pulls))
    if len(repos) <= 1:
        return lambda pull: f"#{pull.number}"
    names = [repo.split("/")[1] for repo in repos]
    is_name_enough = len(set(names)) == len(names)
    return lambda pull: f"{pull.repo.split('/')[1] if is_name_enough else pull.repo}#{pull.number}"


def rows_for(root: Path, session: str) -> list[Row]:
    """The session's open pull requests as the panel lists them. One the daemon has no result for yet is a
    loading row."""
    folder = watch.session_folder(root, session)
    subscribed = subscribed_of(watch.subscriptions(folder), adopted_of(read_json(folder / "state.json")))
    roles_of: dict[tuple[str, int], list[str]] = {}
    pulls: list[Pull] = []
    for (repo, number), roles in subscribed:
        pull = cached_pull(root, repo, number) or loading_pull(repo, number)
        if pull.state == "OPEN":
            pulls.append(pull)
            roles_of[(repo, number)] = [role for role in ROLES if role in {ROLE_OF[one] for one in roles}]
    label = labeler(pulls)
    sections = by_section(pulls, operator_of(read_json(root / "daemon.json")))
    return [Row(section, label(pull), row_state(pull), notes_of(pull), roles_of[(pull.repo, pull.number)], pull)
            for section in SECTIONS for pull in sections[section]]


# Threads

def threads_query(repo: str, number: int) -> str:
    """The GraphQL query for one pull request's threads, which its open drawer lists."""
    owner, name = repo.split("/")
    return (f'query {{ r0: repository(owner: "{owner}", name: "{name}") '
            f'{{ p{number}: pullRequest(number: {number}) {{ {THREAD_FIELDS} }} }} }}')


def thread_of(node: object) -> Optional[Thread]:
    """An unresolved thread as the drawer lists it, or None for a resolved one."""
    if not isinstance(node, dict) or node.get("isResolved") is not False:
        return None
    comments = nodes_of(node.get("comments"))
    comment = record_of(comments[0]) if comments else {}
    line = node.get("line") if is_number(node.get("line")) else node.get("originalLine")
    return Thread(
        path=string_of(node.get("path")) or "",
        line=line if is_number(line) else None,
        is_outdated=node.get("isOutdated") is True,
        author=string_of(record_of(comment.get("author")).get("login")),
        excerpt=first_line(string_of(comment.get("bodyText")) or ""),
        url=string_of(comment.get("url")),
    )


def parse_threads(number: int, stdout: str) -> Optional[list[Thread]]:
    """The unresolved threads, oldest first, in what `gh api graphql` printed for `threads_query`, or None
    when GitHub gave none of the pull request."""
    try:
        body = json.loads(stdout)
    except ValueError:
        return None
    node = record_of(record_of(record_of(body).get("data")).get("r0")).get(f"p{number}")
    if not isinstance(node, dict):
        return None
    return [thread for thread in map(thread_of, nodes_of(node.get("reviewThreads"))) if thread is not None]


def threads_by_file(threads: list[Thread]) -> list[tuple[str, list[Thread]]]:
    """Threads gathered under their files, which come in the order of each file's first thread."""
    files: dict[str, list[Thread]] = {}
    for thread in threads:
        files.setdefault(thread.path, []).append(thread)
    return list(files.items())


def fetch_threads(pull: Pull, gh: Callable[[list[str]], watch.GhResult] = watch.run_gh) -> Optional[list[Thread]]:
    """A pull request's unresolved threads, or None when GitHub cannot say. Each query costs a point of the
    account's GraphQL quota, so a pull request whose cached result counts no unresolved thread asks nothing."""
    if pull.open_threads == 0:
        return []
    result = gh(["api", "graphql", "-f", f"query={threads_query(pull.repo, pull.number)}"])
    return parse_threads(pull.number, result.stdout)
