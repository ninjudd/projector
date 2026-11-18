"""The watch daemon and `project watch`, through a fake `gh` that prints status lines and honors `If-None-Match`.

Each test drives the daemon's pass against a temporary state root, with clocks it moves by hand, so a
lapse, a machine's sleep, a stalled review, a reminder, and a full pass need no waiting. The fake refuses
any argument it does not know, as the watcher scripts' fake did, and serves one fixture per pull request in
the shape the daemon's GraphQL query asks for. Review locks, which `project review` keeps, are stood in for
by patching `review_locks`, `clear_lock`, and `copy_published` on the review module.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections import defaultdict
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional
from unittest import mock
from urllib.parse import parse_qsl, unquote, urlsplit

from projector import agents, cli, relay, review, watch
from projector.inbox import read_json, try_lock, unlock, write_json

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
MODEL = "claude-opus-5-5"
REASONS = {200: "OK", 304: "Not Modified", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


def node(number, *, repo="acme/app", state="OPEN", draft=False, decision=None, head=SHA_A, ref="feature",
         base="main", mergeable="MERGEABLE", merged_at=None, merge_commit=None, author="operator", verdicts=(),
         changes=None, threads=(), comments=(), labels=()):
    """A pull request as the daemon's GraphQL query returns it."""
    return {
        "number": number, "title": f"Change {number}", "url": f"https://github.com/{repo}/pull/{number}", "body": "",
        "state": state, "isDraft": draft, "reviewDecision": decision, "mergeable": mergeable,
        "updatedAt": "2026-10-30T00:00:00Z", "mergedAt": merged_at,
        "mergeCommit": {"oid": merge_commit} if merge_commit else None,
        "headRefOid": head, "headRefName": ref, "baseRefName": base, "author": {"login": author},
        "additions": 3, "deletions": 1, "changedFiles": 1, "commits": {"nodes": []},
        "comments": {"nodes": list(comments)}, "reviews": {"nodes": list(verdicts)},
        "changesRequested": {"nodes": [changes] if changes else []},
        "reviewThreads": {"nodes": list(threads)},
        "labels": {"nodes": [{"name": name} for name in labels]},
    }


def thread(thread_id, *, path="src/x.py", line=3, who="bugbot", body="P1 nil deref", resolved=False, replies=()):
    comments = [{"databaseId": 1, "author": {"login": who}, "body": body}, *replies]
    return {"id": thread_id, "isResolved": resolved, "path": path, "line": line,
            "first": {"nodes": comments[:1]}, "last": {"nodes": comments[-1:]}}


def rest_review(review_id, *, who="codex", body="Body-only concern", sha=SHA_A, state="COMMENTED",
                at="2026-10-30T00:00:00Z", association="COLLABORATOR"):
    return {"id": review_id, "state": state, "user": {"login": who}, "body": body, "commit_id": sha,
            "html_url": review_url(review_id), "submitted_at": at, "author_association": association}


def review_url(review_id: int) -> str:
    return f"https://github.com/acme/app/pull/1#pullrequestreview-{review_id}"


def projector_body(sha, *, model=MODEL, count=1, builds_on=None, body="Clean."):
    """A Projector review body of `sha` with the line counting `count` suggestions, none when `count` is None, and,
    for an incremental review, the lines that name the full review `builds_on`, a `(sha, review id)` pair."""
    marker = (f"<!-- projector-review v=1 verdict=clean projector=0.8.2 model={model} sha={sha} "
              "findings=0 seconds=60 covered=1/1 -->")
    if builds_on is not None:
        marker += (f"\n<!-- projector-incremental v=1 from={builds_on[0]} kind=push insertions=1 deletions=1 files=1 "
                   f"conflicts=0 -->\n\nIncremental review of the change since the full review of "
                   f"[`{builds_on[0][:7]}`]({review_url(builds_on[1])}): one push.")
    counted = "" if count is None else f"<!-- projector-suggestions v=1 count={count} -->\n"
    return f"Projector review\n\n{marker}\n\n{counted}{body}"


def projector_verdict(sha, verdict="changes-requested", *, at="2026-10-12T10:00:00Z", association="COLLABORATOR",
                      model=MODEL, who="operator"):
    """A GraphQL review node by `who` whose body carries the marker line `project review publish` writes."""
    named = f"model={model} " if model else ""
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector=0.7.12 {named}effort=high "
              f"sha={sha} findings=1 seconds=60 covered=1/1 -->")
    return {"author": {"login": who}, "authorAssociation": association, "submittedAt": at,
            "body": f"Projector review\n\n{marker}\n\nSummary"}


FINDING = "<!-- projector-finding v=1 priority=P2 --> A nil deref"


def check_run(name, conclusion="SUCCESS", suite=None, *, workflow=None, at="2026-11-16T22:00:00Z", started=None,
              completed=None):
    """A check run in the head's rollup, as the daemon's query reads it; `conclusion` is None until it completes,
    and `suite` is its workflow run's conclusion, which defaults to its own. A check run with a `workflow` id
    belongs to a run of that workflow started `at`, whose id the two give, so check runs of one run share it; one
    without belongs to no workflow run. `started` and `completed` are the check run's own times."""
    run = None if workflow is None else {"databaseId": workflow * 1_000_000 + int(at[11:19].replace(":", "")),
                                         "createdAt": at, "workflow": {"databaseId": workflow}}
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion, "startedAt": started,
            "completedAt": completed, "checkSuite": {"conclusion": suite or conclusion, "workflowRun": run}}


LATER = "2026-11-16T22:01:00Z"
# When a cancelled run's jobs ran: a job that failed on its own, then the cancel, then the gathering job that
# runs `if: always()` once the jobs it needs end.
FAILED_AT, CANCELLED_AT, GATHERED_AT = "2026-11-16T22:00:20Z", "2026-11-16T22:00:40Z", "2026-11-16T22:00:42Z"


def commit_status(context, state="SUCCESS"):
    return {"__typename": "StatusContext", "context": context, "state": state}


def finding_thread(*replies, resolved=False):
    return thread("T1", body=FINDING, resolved=resolved, replies=replies)


class FakeGitHub:
    """`gh` for one test: GraphQL, `api -i` with `If-None-Match`, `--paginate`, and nothing else."""

    def __init__(self) -> None:
        self.operator = "operator"
        self.repos: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.down = False
        self.graphql_down = False
        self.failing: dict[str, Optional[int]] = {}

    def repository(self, name="acme/app", visibility=None) -> dict:
        found = self.repos.setdefault(name.lower(), {"name": name, "visibility": None, "pulls": {},
                                                     "branches": {"main": SHA_C}})
        if visibility:
            found["visibility"] = visibility
        return found

    def pull_request(self, number, *, repo="acme/app", reviews=(), requested=(), teams=(), contexts=(),
                     **fields) -> None:
        """A pull request, with `contexts` its head's whole rollup, which the GraphQL queries page through as
        GitHub does. The REST pull request does not show `contexts`, so only a full pass sees them change."""
        self.repository(repo)["pulls"][number] = {"node": node(number, repo=repo, **fields), "requested": list(requested),
                                                  "teams": list(teams), "reviews": list(reviews),
                                                  "contexts": list(contexts)}

    def queried(self) -> list[str]:
        found = []
        for call in self.calls:
            if call[:2] == ["api", "graphql"] and any(arg.startswith("n=") for arg in call):
                fields = dict(arg.split("=", 1) for arg in call if re.match(r"^[orn]=", arg))
                found.append(f"{fields['o']}/{fields['r']}#{fields['n']}")
        return found

    def pages_read(self) -> list[str]:
        """The cursor of each later page of a head's rollup contexts that the daemon read."""
        return [arg[2:] for call in self.calls if call[:2] == ["api", "graphql"] for arg in call
                if arg.startswith("c=")]

    def __call__(self, args: list[str]) -> watch.GhResult:
        self.calls.append(list(args))
        if self.down:
            return watch.GhResult(1, "", "dial tcp 127.0.0.1:9: connect: connection refused")
        if args[:1] != ["api"]:
            return self.unsupported(" ".join(args))
        include = paginate = False
        etag = path = None
        fields: dict[str, str] = {}
        rest = list(args[1:])
        while rest:
            arg = rest.pop(0)
            if arg == "-i" and not include:
                include = True
            elif arg == "--paginate" and not paginate:
                paginate = True
            elif arg == "-H" and rest and etag is None:
                header = re.fullmatch(r"If-None-Match: (\S+)", rest.pop(0))
                if header is None:
                    return self.unsupported(arg)
                etag = header.group(1)
            elif arg in ("-f", "-F") and rest and "=" in rest[0]:
                key, value = rest.pop(0).split("=", 1)
                fields[key] = value
            elif path is None and not arg.startswith("-"):
                path = arg
            else:
                return self.unsupported(arg)
        if path == "graphql":
            if include or paginate or etag or set(fields) not in ({"query", "o", "r", "n"}, {"query", "o", "r", "h", "c"}):
                return self.unsupported("graphql with REST flags")
            return self.graphql(fields)
        if fields or path is None or (paginate and include):
            return self.unsupported(str(path))
        if path == "user":
            return watch.GhResult(0, json.dumps({"login": self.operator}))
        return self.rest(path, include, etag, paginate)

    def unsupported(self, what: str) -> watch.GhResult:
        return watch.GhResult(3, "", f"fake gh: unsupported argument: {what}")

    def graphql(self, fields: dict) -> watch.GhResult:
        if self.graphql_down:
            return watch.GhResult(1, "", 'Post "https://api.github.com/graphql": dial tcp: connection refused')
        if "repository(owner:$o,name:$r)" not in fields["query"]:
            return self.unsupported("query")
        if "h" in fields:
            return self.contexts_page(fields)
        repo = self.repos.get(f"{fields['o']}/{fields['r']}".lower())
        pull = (repo or {}).get("pulls", {}).get(int(fields["n"]))
        if pull is None:
            kind = "PullRequest" if repo else "Repository"
            body = {"data": {"repository": {"pullRequest": None} if repo else None},
                    "errors": [{"type": "NOT_FOUND", "message": f"Could not resolve to a {kind}."}]}
            return watch.GhResult(1, json.dumps(body), f"gh: Could not resolve to a {kind}.")
        found = pull["node"]
        if pull["contexts"]:
            found = {**found, "commits": {"nodes": [{"commit": self.rollup(pull["contexts"], fields["query"])}]}}
        data = {"rateLimit": {"cost": 1}, "repository": {"visibility": repo["visibility"], "nameWithOwner": repo["name"],
                                                         "pullRequest": found}}
        return watch.GhResult(0, json.dumps({"data": data}))

    @staticmethod
    def contexts_from(contexts: list[dict], query: str, start: int) -> dict:
        """The page of `contexts` that begins at `start`, as long as the query's `first` asks."""
        end = start + int(re.search(r"contexts\(first:(\d+)", query).group(1))
        return {"pageInfo": {"hasNextPage": end < len(contexts), "endCursor": f"cursor:{min(end, len(contexts))}"},
                "nodes": contexts[start:end]}

    def rollup(self, contexts: list[dict], query: str) -> dict:
        """The head commit with GitHub's counts of its check runs and statuses by state, which cover every
        context, and the state of the status the query names."""
        runs = [found["conclusion"] or "IN_PROGRESS" for found in contexts if found["__typename"] == "CheckRun"]
        statuses = [found for found in contexts if found["__typename"] == "StatusContext"]
        states = [found["state"] for found in statuses]
        named = re.search(r'status\{ context\(name:"([^"]+)"\)', query).group(1)
        own = [found["state"] for found in statuses if found["context"] == named]
        failed = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ERROR"} & {*runs, *states}
        return {"status": {"context": {"state": own[-1]} if own else None} if statuses else None,
                "statusCheckRollup": {"state": "FAILURE" if failed else "SUCCESS", "contexts": {
                    "checkRunCountsByState": [{"state": state, "count": runs.count(state)} for state in set(runs)],
                    "statusContextCountsByState": [{"state": state, "count": states.count(state)} for state in set(states)],
                    **self.contexts_from(contexts, query, 0)}}}

    def contexts_page(self, fields: dict) -> watch.GhResult:
        repo = self.repos.get(f"{fields['o']}/{fields['r']}".lower()) or {"pulls": {}}
        heads = {pull["node"]["headRefOid"]: pull for pull in repo["pulls"].values()}
        cursor = re.fullmatch(r"cursor:(\d+)", fields["c"])
        if fields["h"] not in heads or cursor is None:
            return watch.GhResult(1, json.dumps({"errors": [{"message": "no such commit or cursor"}]}),
                                  "gh: no such commit or cursor")
        page = self.contexts_from(heads[fields["h"]]["contexts"], fields["query"], int(cursor.group(1)))
        data = {"rateLimit": {"cost": 1}, "repository": {"object": {"statusCheckRollup": {"contexts": page}}}}
        return watch.GhResult(0, json.dumps({"data": data}))

    def rest(self, path: str, include: bool, etag: Optional[str], paginate: bool) -> watch.GhResult:
        split = urlsplit(path)
        query = dict(parse_qsl(split.query))
        parts = split.path.split("/")
        if parts[0] != "repos" or len(parts) < 4:
            return self.unsupported(path)
        if split.path in self.failing:
            return self.status(self.failing[split.path], include)
        repo = self.repos.get(f"{parts[1]}/{parts[2]}".lower())
        if repo is None:
            return self.answer(404, {"message": "Not Found"}, include, etag)
        tail = parts[3:]
        open_pulls = [(number, pull) for number, pull in sorted(repo["pulls"].items(), reverse=True)
                      if pull["node"]["state"] == "OPEN"]
        if tail == ["pulls"] and set(query) == {"head", "state"}:
            branch = query["head"].split(":", 1)[1]
            return self.answer(200, [{"number": number} for number, pull in open_pulls
                                     if pull["node"]["headRefName"] == branch], include, etag)
        if tail == ["pulls"] and query == {"state": "open", "per_page": "100", "page": query.get("page", "")}:
            page = int(query["page"])
            listed = [{"number": number, "user": {"login": pull["node"]["author"]["login"]},
                       "requested_reviewers": [{"login": login} for login in pull["requested"]],
                       "requested_teams": [{"slug": slug} for slug in pull["teams"]],
                       "head": {"sha": pull["node"]["headRefOid"]}, "draft": pull["node"]["isDraft"]}
                      for number, pull in open_pulls]
            return self.answer(200, listed[(page - 1) * 100:page * 100], include, etag)
        if tail[:1] == ["pulls"] and len(tail) >= 2 and tail[1].isdigit():
            pull = repo["pulls"].get(int(tail[1]))
            if pull is None:
                return self.answer(404, {"message": "Not Found"}, include, etag)
            if len(tail) == 2 and not query:
                return self.answer(200, pull["node"], include, etag)
            if tail[2:] == ["reviews"] and paginate and query == {"per_page": "100"}:
                return self.answer(200, pull["reviews"], include, etag)
            if tail[2:] == ["reviews"] and set(query) == {"per_page", "page"} and query["per_page"] == "100":
                page = int(query["page"])
                return self.answer(200, pull["reviews"][(page - 1) * 100:page * 100], include, etag)
        if tail[:3] == ["git", "ref", "heads"] and not query:
            branch = unquote("/".join(tail[3:]))
            sha = repo["branches"].get(branch)
            if sha is None:
                return self.answer(404, {"message": "Not Found"}, include, etag)
            return self.answer(200, {"ref": f"refs/heads/{branch}", "object": {"sha": sha}}, include, etag)
        return self.unsupported(path)

    def answer(self, status: int, body: object, include: bool, sent: Optional[str]) -> watch.GhResult:
        text = json.dumps(body, sort_keys=True)
        tag = hashlib.sha1(text.encode()).hexdigest()
        if status == 200 and sent is not None and sent.removeprefix("W/") == f'"{tag}"':
            return self.status(304, include, f'"{tag}"')
        if not include:
            return watch.GhResult(0, text) if status == 200 else watch.GhResult(1, "", f"gh: Not Found (HTTP {status})")
        return self.status(status, include, f'W/"{tag}"' if status == 200 else None, text)

    def status(self, code: Optional[int], include: bool, etag: Optional[str] = None, body: str = "") -> watch.GhResult:
        if code is None:
            return watch.GhResult(1, "", "gh: read: connection reset by peer")
        head = [f"HTTP/2.0 {code} {REASONS[code]}", "Content-Type: application/json; charset=utf-8"]
        if etag:
            head.append(f"Etag: {etag}")
        stdout = "\n".join(head) + "\n\n" + ("" if code == 304 else body)
        return watch.GhResult(0 if code == 200 else 1, stdout if include else "",
                              "" if code == 200 else f"gh: {REASONS[code]} (HTTP {code})")


class Clock:
    """Wall time from now, and a monotonic clock of the daemon's own, both moved by hand."""

    def __init__(self) -> None:
        self.at = time.time()
        self.tick = 1000.0

    def wall(self) -> float:
        return self.at

    def mono(self) -> float:
        return self.tick

    def advance(self, seconds: float) -> None:
        self.at += seconds
        self.tick += seconds


@dataclass(frozen=True)
class Lock:
    path: Path
    repo: str
    number: int
    model_key: str
    sha: str
    loop: Optional[str]
    modified: float


class WatchCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.root = self.scratch / "state" / "projector" / "watch"
        self.review_root = self.scratch / "reviews"
        self.github = FakeGitHub()
        self.clock = Clock()
        self.settings = watch.Settings()
        self.locks: list[Lock] = []
        self.cleared: list[Lock] = []
        self.copies: list[tuple] = []
        self.renewing: set[str] = set()
        self.order = 0
        for name, fake in (("review_locks", self.review_locks), ("clear_lock", self.clear_lock),
                           ("copy_published", self.copy_published)):
            patcher = mock.patch.object(review, name, fake, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.daemon = self.make_daemon()

    def make_daemon(self) -> watch.Daemon:
        return watch.Daemon(self.root, version="1.0", gh=self.github, wall=self.clock.wall, mono=self.clock.mono,
                            settings=self.settings, review_root=self.review_root, pid=4242)

    def review_locks(self, repo, number, root=None):
        return [lock for lock in self.locks if lock.repo.lower() == repo.lower() and lock.number == number]

    def clear_lock(self, lock) -> None:
        self.cleared.append(lock)
        self.locks = [found for found in self.locks if found.path != lock.path]

    def copy_published(self, source, target, repo, number, root=None) -> int:
        self.copies.append((source, target, repo, number))
        return 1

    def lock(self, session: str, number: int = 1, sha: str = SHA_A, model: str = MODEL) -> Lock:
        found = Lock(self.review_root / f"{number}-{session}-{sha}.lock", "acme/app", number, review.model_key(model),
                     sha, session, 1.0)
        self.locks.append(found)
        return found

    def touch(self, found: Lock) -> None:
        self.locks = [replace(lock, modified=lock.modified + 1) if lock.path == found.path else lock
                      for lock in self.locks]

    def folder(self, session: str) -> Path:
        return self.root / "sessions" / session

    def subscribe(self, session: str, *lines: str) -> None:
        watch.edit_subscriptions(self.folder(session), lambda existing: [*existing, *lines])
        self.order += 1
        at = self.clock.wall() + self.order
        os.utime(self.folder(session) / "subscriptions", (at, at))

    def lines(self, session: str) -> list[str]:
        return watch.read_lines(self.folder(session))

    def renew(self, *sessions: str) -> None:
        for session in sessions:
            self.renewing.add(session)
            watch.renew(self.root, session, "mod", self.clock.wall())

    def lapse(self, session: str) -> None:
        self.renewing.discard(session)

    def advance(self, seconds: float) -> None:
        """Move both clocks, renewing each session that has not lapsed, as the mod does once a minute."""
        self.clock.advance(seconds)
        self.renew(*sorted(self.renewing))

    def cycle(self) -> dict[str, list[str]]:
        self.github.calls.clear()
        self.daemon.run_pass()
        return defaultdict(list, {name: self.take(name) for name in watch.session_names(self.root)})

    def take(self, session: str) -> list[str]:
        events = self.folder(session) / "events"
        if not events.exists():
            return []
        text = events.read_text(encoding="utf-8")
        events.write_text("", encoding="utf-8")
        return text.splitlines()

    def state(self, session: str) -> dict:
        return read_json(self.folder(session) / "state.json") or {}

    def owners(self) -> dict:
        return read_json(self.root / "owners.json") or {}

    def rest_paths(self) -> str:
        return "\n".join(" ".join(call) for call in self.github.calls)


def fix_action(number: int, repo: str = "acme/app") -> str:
    return f"run fix-pr on {repo}#{number} in subagent fix-{number}"


def review_action(number: int, session: str, repo: str = "acme/app") -> str:
    return f"run review-pr on {repo}#{number} in subagent review-{number} with --loop {session}"


def rereview(number: int, session: str) -> str:
    return f"run review-pr on acme/app#{number} with --rereview in subagent review-{number} with --loop {session}"


def responded(session: str, how: str = "with every thread resolved", number: int = 1, ref: str = "feature",
              head: str = SHA_A) -> str:
    return (f"RESPONDED acme/app#{number} ({ref}) head={head[:7]}: changes requested {how}; re-review this head, "
            f"and a clean one is a COMMENT, never an approval — {rereview(number, session)}")


def new_pr(number: int, ref: str, session: str, head: str = SHA_A) -> str:
    return (f"NEW PR acme/app#{number} ({ref}) head={head[:7]}: unreviewed, needs an exact-head review — "
            f"{review_action(number, session)}")


def new_head(number: int, ref: str, old: str, new: str, session: str) -> str:
    return (f"NEW HEAD acme/app#{number} ({ref}): {old[:7]} -> {new[:7]}, needs an exact-head review — "
            f"{review_action(number, session)}")


RELAYED = " or on your other machines that share this repository's relay"


def label(change: str, number: int = 1, head: str = SHA_A) -> str:
    meaning = watch.LABEL_CHANGES[change[1:], change[0] == "+"]
    return f"LABEL acme/app#{number} {change} head={head[:8]}: {meaning}"


def unreviewed(number: int, head: str = SHA_A, relayed: bool = False) -> str:
    scope = f"this machine{RELAYED if relayed else ''}"
    return (f"UNREVIEWED acme/app#{number} head={head[:8]}: ready for review, its head is not signed off, and no "
            f"session on {scope} reviews it — tell the user that no session on {scope} reviews acme/app#{number}")


class FixRoleTests(WatchCase):
    """What the `fix` owner hears."""

    def setUp(self) -> None:
        super().setUp()
        # A fixed pull request is a draft its author is working on unless a test says otherwise, so only the tests
        # of review coverage hear UNREVIEWED.
        made = self.github.pull_request
        self.github.pull_request = lambda number, **fields: made(number, **{"draft": True, **fields})

    def fixer(self, *lines: str) -> None:
        self.subscribe("fixer", *lines)
        self.renew("fixer")

    def test_events_come_only_from_subscribed_pull_requests(self) -> None:
        self.github.pull_request(1, threads=[thread("T1", body="P2 off by one")])
        self.github.pull_request(2, draft=True, decision="CHANGES_REQUESTED", threads=[thread("T2")],
                                 changes={"author": {"login": "teammate"}, "body": "Split", "commit": {"oid": SHA_A}},
                                 reviews=[rest_review(9)])
        self.fixer("acme/app#1 fix", "not a subscription")

        events = self.cycle()

        self.assertEqual([f"FINDING acme/app#1 src/x.py:3 [bugbot]: P2 off by one — {fix_action(1)}"], events["fixer"])
        self.assertNotIn("pulls/2", self.rest_paths())
        self.assertEqual(["acme/app#1"], self.github.queried())

    def test_reports_every_kind_of_outstanding_work_on_a_fixed_pull_request(self) -> None:
        marker = f"<!-- projector-finding v=1 priority=P1 sha={SHA_A} -->\nP1 nil deref"
        self.github.pull_request(
            1, draft=False, decision="CHANGES_REQUESTED",
            changes={"author": {"login": "teammate"}, "body": "Please split this", "commit": {"oid": SHA_A}},
            threads=[thread("T1", body=marker), thread("T0", resolved=True)],
            reviews=[rest_review(77, who="codex", body="Body-only concern"),
                     rest_review(78, who="operator", body="<!-- projector-reply v=1 -->"),
                     rest_review(79, who="teammate", body="Approving", state="APPROVED")])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            unreviewed(1),
            f"VERDICT acme/app#1 CHANGES_REQUESTED on aaaaaaaa [teammate]: Please split this — {fix_action(1)}",
            f"FINDING acme/app#1 src/x.py:3 [bugbot]: P1 nil deref — {fix_action(1)}",
            f"REVIEW acme/app#1 by [codex] on aaaaaaaa: Body-only concern — {fix_action(1)}",
        ], events["fixer"])
        self.assertEqual({f"unreviewed:1:{SHA_A}", f"verdict:teammate:{SHA_A}", "T1", "review:77"},
                         set(self.state("fixer")["fix"]["acme/app#1 fix"]))

    def test_a_draft_or_a_signed_off_head_waits_on_no_review(self) -> None:
        # A draft waits on its author, and a clean newest verdict with no open finding has signed its head off.
        self.github.pull_request(1, draft=True)
        self.github.pull_request(2, draft=False, verdicts=[projector_verdict(SHA_A, "clean")])
        self.github.pull_request(3, draft=False, verdicts=[projector_verdict(SHA_A, "clean")],
                                 threads=[finding_thread()])
        self.fixer("acme/app#1 fix", "acme/app#2 fix", "acme/app#3 fix")

        events = self.cycle()

        self.assertEqual([unreviewed(3), f"FINDING acme/app#3 src/x.py:3 [bugbot]: A nil deref — {fix_action(3)}"],
                         events["fixer"], "an open finding keeps #3's clean verdict from signing it off")

    def test_a_ready_pull_request_another_session_reviews_asks_the_fixer_for_nothing(self) -> None:
        self.github.pull_request(1, draft=False)
        self.fixer("acme/app#1 fix")
        self.subscribe("reviewer", f"acme/app#1 review {MODEL} operator")
        self.renew("reviewer")

        events = self.cycle()

        self.assertEqual([], events["fixer"])

    def start_comment(self, sha: str = SHA_A, association: str = "OWNER", who: str = "teammate") -> dict:
        """A live start comment of a review of `sha`, as a review on another machine posts it."""
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock.wall()))
        return {"databaseId": 501, "author": {"login": who}, "authorAssociation": association,
                "createdAt": at, "updatedAt": at,
                "body": f"📽️ Reviewing\n<!-- projector-start v=1 sha={sha} model={MODEL} -->"}

    def test_a_pull_request_another_machine_is_reviewing_asks_the_fixer_for_nothing(self) -> None:
        self.github.pull_request(1, draft=False, comments=[self.start_comment()])
        self.github.pull_request(2, draft=False, comments=[self.start_comment(SHA_B)])
        self.github.pull_request(3, draft=False, comments=[self.start_comment(association="NONE")])
        self.github.pull_request(4, draft=False,
                                 comments=[self.start_comment(association="CONTRIBUTOR", who="operator")])
        self.fixer("acme/app#1 fix", "acme/app#2 fix", "acme/app#3 fix", "acme/app#4 fix")

        events = self.cycle()

        # A live start comment for the head means a review runs somewhere. #2's is for another head, and on a public
        # repository #3's comes from an account whose review would not count, so both are reported. #4's comes from
        # the operator, whose own reviews count whatever their association, as they do for a verdict.
        self.assertEqual([unreviewed(2), unreviewed(3)], events["fixer"])

    def test_a_review_elsewhere_does_not_announce_an_unreviewed_head_twice(self) -> None:
        self.github.pull_request(1, draft=False)
        self.fixer("acme/app#1 fix")
        announced = self.cycle()["fixer"]
        self.github.pull_request(1, draft=False, comments=[self.start_comment()])
        self.advance(900)
        reviewing = self.cycle()["fixer"]
        self.github.pull_request(1, draft=False, verdicts=[projector_verdict(SHA_A)])
        self.advance(900)
        reviewed = self.cycle()["fixer"]
        self.advance(900)
        later = self.cycle()["fixer"]

        self.assertEqual([unreviewed(1)], announced)
        self.assertEqual([], [line for line in reviewing + reviewed + later if line.startswith("UNREVIEWED")])

    def test_a_session_that_fixes_and_reviews_a_pull_request_hears_no_unreviewed(self) -> None:
        self.github.pull_request(1, draft=False)
        self.fixer("acme/app#1 fix", f"acme/app#1 review {MODEL} operator")

        events = self.cycle()

        self.assertEqual([new_pr(1, "feature", "fixer")], events["fixer"])

    def relay_cache(self, *presences: dict, polled: Optional[float] = None, key: bool = True) -> None:
        """Turn the relay on, unless `key` is false, with its cache of acme/app polled at `polled`, now by default,
        holding `presences`, as a relay job caches other machines' agents."""
        self.daemon.relay.key = relay.Key(bytes(range(1, 33)), "inline") if key else None
        path = relay.repo_file(self.root, "relay", "acme/app")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {"repo": "acme/app", "polled_at": watch.stamp(self.clock.wall() if polled is None else polled),
                          "agents": {f"{index:016x}": found for index, found in enumerate(presences)}})

    def presence(self, *targets: str, login: str = "operator", machine: str = "0b8e6a52-0000-4000-8000-000000000002",
                 expires: Optional[float] = None, excluded: tuple[str, ...] = ()) -> dict:
        """A presence from another machine, live unless `expires` says otherwise, whose session reviews `targets`
        and dropped `excluded`."""
        return {"session": "b5e6f7a8-0000-4000-8000-00000000000b", "login": login, "machine": machine,
                "repo": "acme/app", "expires_at": self.clock.wall() + 3600 if expires is None else expires,
                "reviews": [*({"target": target, "model": MODEL, "excluded": True} for target in excluded),
                            *({"target": target, "model": MODEL} for target in targets)]}

    def test_unreviewed_names_the_other_machines_only_while_the_relay_holds_the_key_and_polls_the_repository(
            self) -> None:
        for number in (1, 2, 3):
            self.github.pull_request(number, draft=False)
        self.relay_cache()
        self.fixer("acme/app#1 fix")
        polled = self.cycle()["fixer"]
        self.relay_cache(polled=self.clock.wall() - relay.LEASE)
        self.fixer("acme/app#2 fix")
        stale = self.cycle()["fixer"]
        self.relay_cache(key=False)
        self.fixer("acme/app#3 fix")
        off = self.cycle()["fixer"]

        self.assertEqual([unreviewed(1, relayed=True)], polled)
        self.assertEqual([unreviewed(2)], stale, "a cache a lease old says nothing about the other machines")
        self.assertEqual([unreviewed(3)], off)

    def test_only_a_live_presence_of_the_operator_from_another_machine_holds_back_unreviewed(self) -> None:
        self.github.pull_request(1, draft=False)
        self.github.pull_request(2, draft=False)
        self.fixer("acme/app#1 fix", "acme/app#2 fix")
        self.relay_cache(self.presence("acme/app#1", expires=self.clock.wall() - 1),
                         self.presence("acme/app#1", login="teammate"),
                         self.presence("acme/app#1", machine=agents.this_machine(self.root)),
                         self.presence("acme/app#2"))

        events = self.cycle()

        self.assertEqual([unreviewed(1, relayed=True)], events["fixer"])

    def test_a_remote_review_of_the_repository_covers_only_what_the_operator_wrote_or_is_asked_to_review(
            self) -> None:
        self.github.pull_request(1, draft=False)
        self.github.pull_request(2, draft=False, author="teammate")
        self.github.pull_request(3, draft=False, author="teammate", requested=["operator"])
        self.github.pull_request(4, draft=False)
        self.relay_cache(self.presence("acme/app", excluded=("acme/app#4",)))
        self.fixer("acme/app#1 fix", "acme/app#2 fix", "acme/app#3 fix", "acme/app#4 fix")

        events = self.cycle()

        self.assertEqual([unreviewed(2, relayed=True), unreviewed(4, relayed=True)], events["fixer"],
                         "#2 neither is the operator's nor requests them, and the remote session dropped #4")

    def test_reports_each_review_label_added_or_removed_after_the_first_look(self) -> None:
        self.github.pull_request(1, labels=["clean", "bug"])
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.github.pull_request(1, labels=["bug"])
        unlabeled = self.cycle()["fixer"]
        self.github.pull_request(1, labels=["Reviewing"])
        started = self.cycle()["fixer"]
        self.advance(900)
        quiet = self.cycle()["fixer"]
        self.github.pull_request(1, labels=["clean"])
        finished = self.cycle()["fixer"]

        self.assertEqual([], first)
        self.assertEqual([label("-clean")], unlabeled)
        self.assertEqual([label("+reviewing")], started)
        self.assertEqual([], quiet)
        self.assertEqual([label("+clean"), label("-reviewing")], finished)
        self.assertEqual({"acme/app#1 fix": ["clean"]}, self.state("fixer")["labels"])

    def test_a_label_other_than_a_review_label_reports_nothing(self) -> None:
        self.github.pull_request(1)
        self.fixer("acme/app#1 fix")
        self.cycle()

        self.github.pull_request(1, labels=["bug"])

        self.assertEqual([], self.cycle()["fixer"])

    def test_a_review_owner_hears_no_label_change(self) -> None:
        self.github.pull_request(1, labels=["reviewing"])
        self.subscribe("reviewer", f"acme/app#1 review {MODEL} operator")
        self.renew("reviewer")
        self.cycle()

        self.github.pull_request(1, labels=["clean"])

        self.assertEqual([], [line for line in self.cycle()["reviewer"] if line.startswith("LABEL")])

    def test_a_closed_pull_request_forgets_its_labels(self) -> None:
        self.github.pull_request(1, labels=["clean"])
        self.fixer("acme/app#1 fix")
        self.cycle()

        self.github.pull_request(1, state="CLOSED", labels=["clean"])
        self.cycle()

        self.assertEqual({}, self.state("fixer")["labels"])

    def test_a_projector_review_is_work_only_when_it_lists_suggestions(self) -> None:
        # One review by each model, since only each model's newest verdict on the head stands. The last comes
        # from a release that wrote no count line, so its section is counted.
        earlier = projector_body(SHA_A, model="model-d", count=None,
                                 body="### Suggestions\n\n- `src/x.py:3`: Inline the helper.")
        self.github.pull_request(1, reviews=[
            rest_review(81, who="operator", body=projector_body(SHA_A, model="model-a", count=2)),
            rest_review(82, who="operator", body=projector_body(SHA_A, model="model-b", count=0)),
            rest_review(83, who="operator", body=projector_body(SHA_A, model="model-c", count=None)),
            rest_review(84, who="operator", body=earlier)])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 2 suggestions: Projector review Clean. — {fix_action(1)}",
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 1 suggestion: Projector review ### Suggestions - "
            f"`src/x.py:3`: Inline the helper. — {fix_action(1)}",
        ], events["fixer"])

    def test_a_review_of_an_earlier_head_is_history(self) -> None:
        self.github.pull_request(1, head=SHA_B, reviews=[
            rest_review(71, body="Old concern"),
            rest_review(72, who="operator", body=projector_body(SHA_A), at="2026-10-30T00:00:00Z"),
            rest_review(73, body="Current concern", sha=SHA_B),
            rest_review(74, who="operator", sha=SHA_B, body=projector_body(SHA_B), at="2026-10-30T01:00:00Z")])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"REVIEW acme/app#1 by [codex] on bbbbbbbb: Current concern — {fix_action(1)}",
            f"REVIEW acme/app#1 by [operator] on bbbbbbbb, listing 1 suggestion: Projector review Clean. — {fix_action(1)}",
        ], events["fixer"])

    def test_suggestions_an_incremental_review_carries_from_its_full_review_are_work(self) -> None:
        # The fixer gains the role at head B, whose incremental review lists none of its own, so only the
        # full review of A says what stands, as `project review suggestions` reads it.
        self.github.pull_request(1, head=SHA_B, reviews=[
            rest_review(81, who="operator", body=projector_body(SHA_A, count=2), at="2026-10-30T00:00:00Z"),
            rest_review(82, who="operator", sha=SHA_B, body=projector_body(SHA_B, count=None, builds_on=(SHA_A, 81)),
                        at="2026-10-30T01:00:00Z")])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 2 suggestions: Projector review Clean. — {fix_action(1)}",
        ], events["fixer"])

    def test_a_full_reviews_suggestions_are_announced_once_across_the_heads_they_stand_on(self) -> None:
        self.github.pull_request(1, reviews=[
            rest_review(81, who="operator", body=projector_body(SHA_A, count=2), at="2026-10-30T00:00:00Z")])
        self.fixer("acme/app#1 fix")
        first = self.cycle()["fixer"]

        pull = self.github.repository()["pulls"][1]
        pull["node"]["headRefOid"] = SHA_B
        between = self.cycle()["fixer"]
        pull["reviews"].append(rest_review(82, who="operator", sha=SHA_B, at="2026-10-30T01:00:00Z",
                                           body=projector_body(SHA_B, count=None, builds_on=(SHA_A, 81))))
        self.advance(60)
        after = self.cycle()["fixer"]

        self.assertEqual(1, len(first), first)
        self.assertEqual([], between)
        self.assertEqual([], after)

    def test_a_projector_review_counts_as_project_review_suggestions_counts_it(self) -> None:
        # In a public repository, an account the repository does not let in gives no verdict, so its
        # suggestions stand only when it is the operator's.
        self.github.pull_request(1, reviews=[
            rest_review(81, who="stranger", association="NONE", body=projector_body(SHA_A, model="model-a")),
            rest_review(82, who="operator", association="NONE", body=projector_body(SHA_A, model="model-b"))])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 1 suggestion: Projector review Clean. — {fix_action(1)}",
        ], events["fixer"])

    def test_a_later_pass_repeats_only_after_the_renotify_interval(self) -> None:
        self.github.pull_request(1, draft=False, threads=[thread("T1")], reviews=[rest_review(5)])
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.advance(60)
        second = self.cycle()["fixer"]
        self.advance(900)
        third = self.cycle()["fixer"]

        self.assertEqual(3, len(first), first)
        self.assertEqual([], second)
        self.assertEqual([f"FINDING acme/app#1 (still open) src/x.py:3 [bugbot]: P1 nil deref — {fix_action(1)}"],
                         third)

    def test_reports_a_conflict_with_the_base_until_a_push_clears_it(self) -> None:
        self.github.pull_request(1, base="parent", mergeable="CONFLICTING")
        self.github.repository()["branches"]["parent"] = SHA_C
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.advance(60)
        second = self.cycle()["fixer"]
        self.advance(900)
        third = self.cycle()["fixer"]
        self.github.pull_request(1, head=SHA_B, base="parent", mergeable="MERGEABLE")
        merged = self.cycle()["fixer"]

        self.assertEqual([f"CONFLICT acme/app#1 head=aaaaaaaa base=parent: the branch conflicts with its base; merge "
                          f"the base and resolve it — {fix_action(1)}"], first)
        self.assertEqual([], second)
        self.assertEqual([f"CONFLICT acme/app#1 (still open) head=aaaaaaaa base=parent: still conflicts with its base "
                          f"— {fix_action(1)}"], third)
        self.assertEqual([], merged)
        self.assertEqual({}, self.state("fixer")["fix"]["acme/app#1 fix"])

    def test_a_base_that_moves_into_conflict_announces_under_the_same_head(self) -> None:
        self.github.pull_request(1)
        self.fixer("acme/app#1 fix")
        self.assertEqual([], self.cycle()["fixer"])

        self.github.pull_request(1, mergeable="CONFLICTING")
        events = self.cycle()["fixer"]

        self.assertEqual(1, len(events), events)
        self.assertTrue(events[0].startswith("CONFLICT acme/app#1 head=aaaaaaaa base=main"), events)

    def test_a_conflict_is_not_new_again_after_github_recomputes_it(self) -> None:
        self.github.pull_request(1, mergeable="CONFLICTING")
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.github.pull_request(1, mergeable="UNKNOWN")
        recomputing = self.cycle()["fixer"]
        self.github.pull_request(1, mergeable="CONFLICTING")
        recomputed = self.cycle()["fixer"]

        self.assertEqual(1, len(first), first)
        self.assertEqual([], recomputing)
        self.assertEqual([], recomputed)
        self.assertEqual([f"conflict:1:{SHA_A}"], list(self.state("fixer")["fix"]["acme/app#1 fix"]))

    def test_an_unsettled_mergeability_is_queried_each_pass_and_is_not_a_conflict(self) -> None:
        self.github.pull_request(1, mergeable="UNKNOWN")
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        unsettled = self.github.queried()
        second = self.cycle()["fixer"]
        again = self.github.queried()
        self.github.pull_request(1, mergeable="MERGEABLE")
        self.cycle()
        self.cycle()
        settled = self.github.queried()

        self.assertEqual(([], []), (first, second))
        self.assertEqual((["acme/app#1"], ["acme/app#1"]), (unsettled, again))
        self.assertEqual([], settled)
        self.assertEqual({}, self.state("fixer")["fix"]["acme/app#1 fix"])

    def test_a_failed_check_is_announced_once_and_again_only_after_the_renotify_interval(self) -> None:
        self.github.pull_request(1, contexts=[check_run("check", "FAILURE"), check_run("lint", "FAILURE"),
                                              check_run("docs"), commit_status("deploy")])
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.advance(60)
        second = self.cycle()["fixer"]
        self.advance(900)
        third = self.cycle()["fixer"]

        self.assertEqual([f"CHECKS acme/app#1 head=aaaaaaaa failed: check, lint — {fix_action(1)}"], first)
        self.assertEqual([], second)
        self.assertEqual([f"CHECKS acme/app#1 (still open) head=aaaaaaaa failed: check, lint — {fix_action(1)}"],
                         third)

    def test_only_a_context_that_failed_is_a_ci_failure(self) -> None:
        cases = [
            (check_run("check", "FAILURE"), "check"),
            (check_run("check", "TIMED_OUT"), "check"),
            (check_run("check", "STARTUP_FAILURE"), "check"),
            (commit_status("ci/build", "FAILURE"), "ci/build"),
            (commit_status("ci/build", "ERROR"), "ci/build"),
            (commit_status(review.REVIEW_CONTEXT, "FAILURE"), None),
            (check_run("check", "ACTION_REQUIRED"), None),
            (check_run("check", None), None),
            (check_run("check", "CANCELLED"), None),
            (check_run("check", "SKIPPED"), None),
            (check_run("check", "NEUTRAL"), None),
            (commit_status("ci/build", "PENDING"), None),
        ]
        for number, (context, _) in enumerate(cases, 1):
            self.github.pull_request(number, contexts=[context])
        self.fixer(*(f"acme/app#{number} fix" for number in range(1, len(cases) + 1)))

        events = self.cycle()["fixer"]

        for number, (context, failed) in enumerate(cases, 1):
            with self.subTest(context=context):
                expected = [] if failed is None else [
                    f"CHECKS acme/app#{number} head=aaaaaaaa failed: {failed} — {fix_action(number)}"]
                self.assertEqual(expected, [event for event in events if event.startswith(f"CHECKS acme/app#{number} ")])

    def assert_checks(self, cases: dict) -> None:
        """Give each case's contexts a pull request of its own, and check the CHECKS event each one hears: the
        failures it names, or none for None."""
        for number, (contexts, _) in enumerate(cases.values(), 1):
            self.github.pull_request(number, contexts=contexts)
        self.fixer(*(f"acme/app#{number} fix" for number in range(1, len(cases) + 1)))

        events = self.cycle()["fixer"]

        for number, (case, (_, failed)) in enumerate(cases.items(), 1):
            with self.subTest(case=case):
                expected = [] if failed is None else [
                    f"CHECKS acme/app#{number} head=aaaaaaaa failed: {failed} — {fix_action(number)}"]
                self.assertEqual(expected, [event for event in events if event.startswith(f"CHECKS acme/app#{number} ")])

    def test_a_failure_the_cancel_caused_is_no_ci_failure(self) -> None:
        # A label starts a run that cancels the one in progress. Its gathering job, which runs `if: always()`
        # once the jobs it needs end, starts after the cancel and fails though nothing in the code did.
        slow = check_run("slow", "CANCELLED", workflow=1, started="2026-11-16T22:00:05Z", completed=CANCELLED_AT)
        gathering = check_run("complete", "FAILURE", "CANCELLED", workflow=1, started=GATHERED_AT,
                              completed="2026-11-16T22:00:45Z")
        self.assert_checks({
            "nothing ran since": ([slow, gathering], None),
            "the newer run skipped every job": ([slow, gathering, check_run("slow", "SKIPPED", workflow=1, at=LATER),
                                                 check_run("complete", "SKIPPED", workflow=1, at=LATER)], None),
            "it started as the cancel ended": ([slow, {**gathering, "startedAt": CANCELLED_AT}], None),
            # A failure that ended before the cancel is the code's, whatever came after.
            "a check failed before the cancel": ([check_run("lint", "FAILURE", "CANCELLED", workflow=1,
                                                            started="2026-11-16T22:00:05Z", completed=FAILED_AT),
                                                  slow, gathering], "lint"),
            # Without a cancelled check run of its own run, nothing says when the cancel came.
            "the cancel time is unknown": ([gathering], "complete"),
            "only another run was cancelled": ([check_run("slow", "CANCELLED", workflow=2, completed=CANCELLED_AT),
                                                gathering], "complete"),
        })

    def test_a_failure_in_a_cancelled_run_that_a_newer_run_tests_again_is_no_ci_failure(self) -> None:
        # The run's `lint` failed on its own before a label's run cancelled it, and GitHub keeps both runs' checks
        # on the head. The newer run's own result stands for the code.
        cancelled = [check_run("lint", "FAILURE", "CANCELLED", workflow=1, started="2026-11-16T22:00:05Z",
                               completed=FAILED_AT),
                     check_run("slow", "CANCELLED", workflow=1, completed=CANCELLED_AT)]
        self.assert_checks({
            "the newer run passed": ([*cancelled, check_run("lint", workflow=1, at=LATER),
                                      check_run("slow", workflow=1, at=LATER)], None),
            "the newer run failed": ([*cancelled, check_run("lint", "FAILURE", workflow=1, at=LATER),
                                      check_run("slow", "FAILURE", workflow=1, at=LATER)], "lint, slow"),
            # Its other jobs have not started, so they have no check runs yet.
            "the newer run still runs": ([*cancelled, check_run("slow", None, workflow=1, at=LATER)], None),
            "nothing ran since": (cancelled, "lint"),
            "the newer run skipped the job": ([*cancelled, check_run("lint", "SKIPPED", workflow=1, at=LATER),
                                               check_run("slow", "SKIPPED", workflow=1, at=LATER)], "lint"),
            "only another workflow ran since": ([*cancelled, check_run("lint", workflow=2, at=LATER)], "lint"),
            "the cancelled run is the newer one": ([*cancelled, check_run("lint", workflow=1,
                                                                          at="2026-11-16T21:59:00Z")], "lint"),
        })

    def test_a_check_that_failed_before_its_run_was_cancelled_is_a_ci_failure(self) -> None:
        # CI starts on `labeled`, and a job-level `if` skips every job for a review's label, whose run cancels
        # the run in which `lint` had already failed. The skipped `lint` tests nothing, so the failure stands,
        # while the gathering job that failed after the cancel does not.
        self.github.pull_request(1, contexts=[
            check_run("lint", "FAILURE", "CANCELLED", workflow=1, started="2026-11-16T22:00:05Z", completed=FAILED_AT),
            check_run("test", "CANCELLED", workflow=1, started="2026-11-16T22:00:05Z", completed=CANCELLED_AT),
            check_run("complete", "FAILURE", "CANCELLED", workflow=1, started=GATHERED_AT),
            check_run("lint", "SKIPPED", workflow=1, at=LATER),
            check_run("test", "SKIPPED", workflow=1, at=LATER),
            check_run("complete", "SKIPPED", workflow=1, at=LATER)])
        # A check run that no workflow run made is never a cancelled run's, whatever its suite concluded.
        self.github.pull_request(2, contexts=[check_run("external", "FAILURE", "CANCELLED", started=GATHERED_AT),
                                              check_run("other", "CANCELLED", completed=CANCELLED_AT)])
        self.fixer("acme/app#1 fix", "acme/app#2 fix")

        events = self.cycle()["fixer"]

        self.assertEqual([f"CHECKS acme/app#1 head=aaaaaaaa failed: lint — {fix_action(1)}",
                          f"CHECKS acme/app#2 head=aaaaaaaa failed: external — {fix_action(2)}"],
                         sorted(event for event in events if event.startswith("CHECKS ")))

    def test_a_failure_in_a_cancelled_run_reads_no_later_page_of_contexts_for_one_it_cannot_find(self) -> None:
        # The rollup counts the cancelled run's failure, which the first page holds, so nothing is left to find.
        self.github.pull_request(1, contexts=[check_run("complete", "FAILURE", "CANCELLED", workflow=1),
                                              check_run("complete", workflow=1, at=LATER),
                                              *(check_run(f"job {index}") for index in range(3, 26))])
        self.fixer("acme/app#1 fix")

        events = self.cycle()["fixer"]

        self.assertEqual(([], []), (events, self.github.pages_read()))

    def test_a_failed_check_past_the_first_page_of_contexts_is_found_on_a_later_page(self) -> None:
        self.github.pull_request(1, contexts=[*(check_run(f"job {index}") for index in range(1, 25)),
                                              check_run("job 25", "FAILURE")])
        self.fixer("acme/app#1 fix")

        events = self.cycle()["fixer"]

        self.assertEqual([f"CHECKS acme/app#1 head=aaaaaaaa failed: job 25 — {fix_action(1)}"], events)
        self.assertEqual(["cursor:20"], self.github.pages_read())

    def test_a_failed_projector_review_status_reads_no_later_page_of_contexts(self) -> None:
        self.github.pull_request(1, contexts=[commit_status(review.REVIEW_CONTEXT, "FAILURE"),
                                              *(check_run(f"job {index}") for index in range(2, 26))])
        self.fixer("acme/app#1 fix")

        events = self.cycle()["fixer"]

        self.assertEqual(([], []), (events, self.github.pages_read()))

    def test_a_new_head_hears_no_checks_until_a_context_on_it_fails(self) -> None:
        self.github.pull_request(1, contexts=[check_run("check", "FAILURE")])
        self.fixer("acme/app#1 fix")
        failed = self.cycle()["fixer"]

        self.github.pull_request(1, head=SHA_B, contexts=[check_run("check", None)])
        pushed = self.cycle()["fixer"]
        self.advance(900)
        running = self.cycle()["fixer"]
        self.github.pull_request(1, head=SHA_B, contexts=[check_run("check", "FAILURE")])
        self.advance(600)
        failed_again = self.cycle()["fixer"]
        self.github.pull_request(1, head=SHA_C, contexts=[check_run("check", "FAILURE")])
        failed_at_once = self.cycle()["fixer"]

        self.assertEqual([f"CHECKS acme/app#1 head=aaaaaaaa failed: check — {fix_action(1)}"], failed)
        self.assertEqual(([], []), (pushed, running))
        self.assertEqual([f"CHECKS acme/app#1 head=bbbbbbbb failed: check — {fix_action(1)}"], failed_again)
        self.assertEqual([f"CHECKS acme/app#1 head=cccccccc failed: check — {fix_action(1)}"], failed_at_once)

    def test_a_merged_pull_request_is_announced_once_and_its_lines_end(self) -> None:
        self.github.pull_request(1, draft=False, threads=[thread("T1")])
        self.fixer("acme/app#1 fix", "!acme/app#1 review claude-opus-5-5 operator")
        self.assertEqual(2, len(self.cycle()["fixer"]))

        self.github.pull_request(1, state="MERGED", draft=True, threads=[thread("T1")],
                                 merged_at="2026-10-23T01:36:44Z", merge_commit="ea9a364" + "0" * 33)
        events = self.cycle()["fixer"]
        after = self.cycle()["fixer"]

        self.assertEqual(["MERGED acme/app#1 into main at 2026-10-23T01:36:44Z (merge commit ea9a3640) — finish "
                          "subagent fix-1"], events)
        self.assertEqual(["!acme/app#1 review claude-opus-5-5 operator"], self.lines("fixer"))
        self.assertEqual([], after)
        self.assertEqual([], self.github.queried())

    def test_a_closed_pull_request_is_announced_once_without_a_merge(self) -> None:
        self.github.pull_request(2, state="CLOSED", threads=[thread("T2")])
        self.fixer("acme/app#2 fix")

        self.assertEqual(["CLOSED acme/app#2 without merging — finish subagent fix-2"], self.cycle()["fixer"])
        self.assertEqual([], self.cycle()["fixer"])
        self.assertEqual([], self.lines("fixer"))

    def test_a_merge_seen_again_after_its_line_returns_is_not_announced_twice(self) -> None:
        self.github.pull_request(1, state="MERGED")
        self.fixer("acme/app#1 fix")
        self.assertEqual(1, len(self.cycle()["fixer"]))

        self.subscribe("fixer", "acme/app#1 fix")
        again = self.cycle()["fixer"]

        self.assertEqual([], again)
        self.assertEqual([], self.lines("fixer"))

    def test_a_failed_query_announces_no_merge(self) -> None:
        self.github.pull_request(1, state="MERGED")
        self.fixer("acme/app#1 fix")
        self.github.graphql_down = True

        failed = self.cycle()["fixer"]
        self.github.graphql_down = False
        recovered = self.cycle()["fixer"]

        self.assertEqual([], failed)
        self.assertEqual(["MERGED acme/app#1 into main — finish subagent fix-1"], recovered)

    def test_the_subscriptions_are_read_again_on_every_pass(self) -> None:
        self.github.pull_request(1, threads=[thread("T1")])
        self.github.pull_request(2, threads=[thread("T2", body="P3 typo")])
        self.fixer("acme/app#1 fix")
        self.cycle()

        self.subscribe("fixer", "acme/app#2 fix")
        added = self.cycle()["fixer"]
        watch.remove(self.root, "fixer", "acme/app", 1, "fix")
        removed = self.cycle()["fixer"]

        self.assertEqual([f"FINDING acme/app#2 src/x.py:3 [bugbot]: P3 typo — {fix_action(2)}"], added)
        self.assertEqual([], removed)
        self.assertEqual(["acme/app#2 fix"], list(self.state("fixer")["fix"]))

    def test_carries_the_last_result_forward_when_github_cannot_be_reached(self) -> None:
        self.github.pull_request(1, threads=[thread("T1")])
        self.fixer("acme/app#1 fix")
        self.cycle()
        before = (self.state("fixer"), read_json(watch.pull_cache(self.root, "acme/app", 1)))

        self.github.down = True
        events = self.cycle()["fixer"]

        self.assertEqual([], events)
        self.assertEqual(before, (self.state("fixer"), read_json(watch.pull_cache(self.root, "acme/app", 1))))
        self.assertEqual(["acme/app#1 fix"], self.lines("fixer"))

    def test_an_exclusion_keeps_a_pull_request_out_wherever_it_sits_and_in_any_letter_case(self) -> None:
        self.github.pull_request(1, threads=[thread("T1", body="P2 off by one")])
        self.github.pull_request(2, threads=[thread("T2", body="P3 typo")])
        self.fixer("!ACME/App#2 fix", "acme/app#1 fix", "acme/app#2 fix")

        events = self.cycle()["fixer"]

        self.assertEqual([f"FINDING acme/app#1 src/x.py:3 [bugbot]: P2 off by one — {fix_action(1)}"], events)
        self.assertNotIn("pulls/2", self.rest_paths())

    def test_a_number_that_names_no_pull_request_is_missing_once_and_its_line_goes(self) -> None:
        self.github.pull_request(1)
        self.fixer("acme/app#1 fix", "acme/app#7 fix", "!acme/app#7 review claude-opus-5-5 operator")

        events = self.cycle()["fixer"]
        again = self.cycle()["fixer"]

        self.assertEqual(["MISSING acme/app#7: no such repository or pull request, or the operator cannot see it, so "
                          "the watch removed its subscription — tell the user"], events)
        self.assertEqual([], again)
        self.assertEqual(["acme/app#1 fix", "!acme/app#7 review claude-opus-5-5 operator"], self.lines("fixer"))

    def test_a_misspelled_repository_is_missing_rather_than_unreachable(self) -> None:
        self.github.pull_request(1)
        self.fixer("acme/ap#1 fix")

        events = self.cycle()["fixer"]

        self.assertEqual(1, len(events), events)
        self.assertTrue(events[0].startswith("MISSING acme/ap#1: no such repository or pull request"), events)

    def test_an_unreachable_github_is_never_missing(self) -> None:
        self.github.pull_request(1)
        self.fixer("acme/app#1 fix")
        self.github.down = True

        self.assertEqual([], self.cycle()["fixer"])
        self.assertEqual(["acme/app#1 fix"], self.lines("fixer"))


class ReviewRoleTests(WatchCase):
    """What the `review` owner hears."""

    def reviewer(self, *numbers: int, model: str = MODEL, login: str = "operator") -> None:
        self.subscribe("reviewer", *(f"acme/app#{number} review {model} {login}" for number in numbers))
        self.renew("reviewer")

    def test_events_come_only_from_subscribed_pull_requests(self) -> None:
        self.github.pull_request(1, ref="feature-a")
        self.github.pull_request(2, ref="feature-b")
        self.reviewer(1)

        events = self.cycle()["reviewer"]

        self.assertEqual([new_pr(1, "feature-a", "reviewer")], events)
        self.assertEqual(["acme/app#1"], self.github.queried())
        self.assertEqual({"head": SHA_A, "flag": f"{SHA_A}:-", "announced": self.clock.wall()},
                         self.state("reviewer")["review"][f"acme/app#1 review {MODEL}"])

    def watch_repository(self) -> None:
        self.subscribe("reviewer", f"acme/app review {MODEL} operator")
        self.renew("reviewer")

    def test_a_draft_waits_for_ready_under_a_repository_subscription(self) -> None:
        self.github.pull_request(1, draft=True)
        self.watch_repository()
        drafted = self.cycle()["reviewer"]
        self.github.pull_request(1, draft=True, head=SHA_B)
        pushed = self.cycle()["reviewer"]
        self.advance(3600)
        later = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_B)
        ready = self.cycle()["reviewer"]
        again = self.cycle()["reviewer"]

        self.assertEqual(([], [], []), (drafted, pushed, later), "the author is still working on a draft")
        self.assertEqual([new_pr(1, "feature", "reviewer", SHA_B)], ready)
        self.assertEqual([], again)

    def test_a_draft_under_a_repository_subscription_asks_for_no_re_review(self) -> None:
        changes = projector_verdict(SHA_A)
        self.github.pull_request(1, verdicts=[changes], threads=[thread("T1")])
        self.watch_repository()
        self.cycle()

        self.github.pull_request(1, draft=True, verdicts=[changes], threads=[thread("T1", resolved=True)])
        drafted = self.cycle()["reviewer"]
        self.github.pull_request(1, verdicts=[changes], threads=[thread("T1", resolved=True)])
        ready = self.cycle()["reviewer"]

        self.assertEqual([], drafted)
        self.assertEqual([responded("reviewer")], ready)

    def test_marking_a_draft_ready_announces_at_once_a_head_no_review_read(self) -> None:
        self.github.pull_request(1)
        self.watch_repository()
        announced = self.cycle()["reviewer"]
        self.github.pull_request(1, draft=True)
        drafted = self.cycle()["reviewer"]
        self.github.pull_request(1)
        ready = self.cycle()["reviewer"]

        self.assertEqual([new_pr(1, "feature", "reviewer")], announced)
        self.assertEqual([], drafted)
        self.assertEqual([f"NEW HEAD acme/app#1 (still open) (feature) head=aaaaaaa: still needs an exact-head review "
                          f"— {review_action(1, 'reviewer')}"], ready, "the head waited without a review")

    def test_a_head_pushed_to_a_draft_is_a_new_head_once_it_is_ready(self) -> None:
        clean = [projector_verdict(SHA_A, "clean")]
        self.github.pull_request(1, verdicts=clean)
        self.watch_repository()
        known = self.cycle()["reviewer"]
        self.github.pull_request(1, draft=True, head=SHA_B, verdicts=clean)
        drafted = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_B, verdicts=clean)
        ready = self.cycle()["reviewer"]

        self.assertEqual(([], []), (known, drafted))
        self.assertEqual([new_head(1, "feature", SHA_A, SHA_B, "reviewer")], ready)

    def test_a_pull_request_subscription_announces_a_draft(self) -> None:
        # Running review-pr on a pull request asks for its review now, draft or not.
        self.github.pull_request(1, draft=True)
        self.reviewer(1)

        self.assertEqual([new_pr(1, "feature", "reviewer")], self.cycle()["reviewer"])

    def test_reports_a_moved_head_then_a_response_once_per_head(self) -> None:
        self.github.pull_request(1, head=SHA_B, draft=True)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True)
        moved = self.cycle()["reviewer"]
        suppressed = self.cycle()["reviewer"]
        changes = projector_verdict(SHA_A)
        self.github.pull_request(1, head=SHA_A, draft=True, verdicts=[changes], threads=[thread("T1")])
        under_review = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True, verdicts=[changes], threads=[thread("T1", resolved=True)])
        answered = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, verdicts=[changes], threads=[thread("T2")])
        reopened = self.cycle()["reviewer"]
        flag = self.state("reviewer")["review"][f"acme/app#1 review {MODEL}"]["flag"]
        self.github.pull_request(1, head=SHA_A, verdicts=[changes], threads=[thread("T2", resolved=True)])
        answered_again = self.cycle()["reviewer"]

        self.assertEqual([new_head(1, "feature", SHA_B, SHA_A, "reviewer")], moved)
        self.assertEqual(([], [], [], []), (suppressed, under_review, quiet, reopened))
        self.assertEqual("-", flag)
        self.assertEqual([responded("reviewer")], answered)
        self.assertEqual([responded("reviewer")], answered_again)

    def test_a_head_with_no_change_request_waits_on_nothing(self) -> None:
        # Draft state is the author's, so an unchanged draft whose threads are all resolved needs no re-review.
        self.github.pull_request(1, head=SHA_A, draft=True)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True, threads=[thread("T1", resolved=True)])
        unsettled = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True, verdicts=[projector_verdict(SHA_A, "clean")],
                                 threads=[thread("T1", resolved=True)])
        clean = self.cycle()["reviewer"]

        self.assertEqual(([], []), (unsettled, clean))

    def test_reports_an_author_reply_on_an_open_finding_once_per_reply(self) -> None:
        finding = {"databaseId": 11, "body": FINDING}

        def finding_on(*replies):
            found = thread("T1", body=FINDING, replies=replies)
            found["first"]["nodes"] = [finding]
            return found

        changes = [projector_verdict(SHA_A)]
        self.github.pull_request(1, head=SHA_A, threads=[finding_on()])
        self.reviewer(1)
        self.cycle()

        decline = {"databaseId": 12, "body": "<!-- projector-reply v=1 --> Declined: the caller checks for nil."}
        self.github.pull_request(1, head=SHA_A, verdicts=changes, threads=[finding_on(decline)])
        replied = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]
        kept = {"databaseId": 13, "body": "<!-- projector-verify v=1 result=kept --> The second caller does not."}
        self.github.pull_request(1, head=SHA_A, verdicts=changes, threads=[finding_on(decline, kept)])
        after_verify = self.cycle()["reviewer"]
        again = {"databaseId": 14, "body": "Fair, but that caller is dead code."}
        self.github.pull_request(1, head=SHA_A, verdicts=changes, threads=[finding_on(decline, kept, again)])
        replied_again = self.cycle()["reviewer"]

        self.assertEqual([responded("reviewer", "and the author replied on an open finding")], replied)
        self.assertEqual([], quiet)
        self.assertEqual([], after_verify, "the reviewer's own verify reply is not an answer")
        self.assertEqual(1, len(replied_again))
        self.assertIn("the author replied on an open finding", replied_again[0])

    def test_resolving_a_replied_finding_after_a_push_does_not_re_request_the_head(self) -> None:
        reply = {"databaseId": 32, "body": "<!-- projector-reply v=1 --> Fixed in aaaaaaa."}
        self.github.pull_request(1, head=SHA_B, draft=True)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread(reply)])
        moved = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread(reply, resolved=True)])
        resolved = self.cycle()["reviewer"]

        self.assertEqual([new_head(1, "feature", SHA_B, SHA_A, "reviewer")], moved)
        self.assertEqual([], resolved)

    def test_a_reply_on_another_reviewers_thread_is_not_a_response(self) -> None:
        self.github.pull_request(1, head=SHA_A, draft=True)
        self.reviewer(1)
        self.cycle()

        human = thread("T1", who="alice", body="Should this be a constant?",
                       replies=[{"databaseId": 21, "author": {"login": "operator"}, "body": "Done."}])
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[human])

        self.assertEqual([], self.cycle()["reviewer"])

    def test_converting_a_reviewed_head_to_a_draft_asks_for_nothing(self) -> None:
        # The author holds a signed-off pull request in draft while building a change on it.
        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A, "clean")])
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True, verdicts=[projector_verdict(SHA_A, "clean")])
        held = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A, "clean")])
        ready_again = self.cycle()["reviewer"]

        self.assertEqual(([], []), (held, ready_again))

    def test_an_old_hold_comment_silences_nothing(self) -> None:
        # An older Projector's hold comment, left on a pull request, holds nothing for this one.
        hold = {"author": {"login": "operator"},
                "body": "Held in draft while the following changes are implemented:\n\n- Rename `x`\n\n"
                        "<!-- projector-hold v=1 -->\n"}
        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)], threads=[thread("T1")])
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)], comments=[hold],
                                 threads=[thread("T1", resolved=True)])

        self.assertEqual([responded("reviewer")], self.cycle()["reviewer"])

    def test_a_head_this_reviewer_already_reviewed_is_known_without_a_word(self) -> None:
        self.github.pull_request(1, ref="reviewed", verdicts=[projector_verdict(SHA_A, "clean")])
        self.github.pull_request(2, ref="other-model", verdicts=[projector_verdict(SHA_A, "clean", model="gpt-6")])
        self.github.pull_request(3, ref="unreviewed")
        self.github.pull_request(4, ref="older-head", verdicts=[projector_verdict(SHA_B, "clean")])
        self.github.pull_request(5, ref="no-model", verdicts=[projector_verdict(SHA_A, "clean", model=None)])
        # The model ID the host reports, with the context-window variant `project review` ignores, and the login
        # in another case.
        self.reviewer(1, 2, 3, 4, 5, model="Claude-Opus-5-5[1m]", login="Operator")

        first = self.cycle()["reviewer"]
        again = self.cycle()["reviewer"]

        self.assertEqual([new_pr(2, "other-model", "reviewer"), new_pr(3, "unreviewed", "reviewer"),
                          new_pr(4, "older-head", "reviewer"), new_pr(5, "no-model", "reviewer")], first)
        self.assertEqual({"head": SHA_A, "flag": "-", "announced": None},
                         self.state("reviewer")["review"][f"acme/app#1 review {MODEL}"])
        self.assertEqual([], again)

    def test_only_the_reviewers_own_verdict_makes_a_head_known(self) -> None:
        self.github.pull_request(1, ref="teammate", author="teammate",
                                 verdicts=[projector_verdict(SHA_A, "clean", who="teammate")])
        self.github.pull_request(2, ref="passerby", author="teammate",
                                 verdicts=[projector_verdict(SHA_A, "clean", who="passerby", association="NONE")])
        # The reviewer's own verdict counts whatever its association, as `project review status` counts it.
        self.github.pull_request(3, ref="outside-reviewer", author="teammate",
                                 verdicts=[projector_verdict(SHA_A, "clean", association="NONE")])
        self.reviewer(1, 2, 3)

        self.assertEqual([new_pr(1, "teammate", "reviewer"), new_pr(2, "passerby", "reviewer")],
                         self.cycle()["reviewer"])

    def test_a_reviewed_head_whose_author_answered_asks_for_a_re_review(self) -> None:
        self.github.pull_request(1, draft=True, verdicts=[projector_verdict(SHA_A)],
                                 threads=[thread("T1", resolved=True)])
        self.reviewer(1)

        known = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]

        self.assertEqual([responded("reviewer")], known)
        self.assertEqual([], quiet)

    def test_a_reviewed_head_that_moved_is_a_new_head(self) -> None:
        self.github.pull_request(1, ref="moved", head=SHA_B, verdicts=[projector_verdict(SHA_B, "clean")])
        self.github.pull_request(2, ref="seen", verdicts=[projector_verdict(SHA_A, "clean")])
        self.reviewer(1, 2)
        self.assertEqual([], self.cycle()["reviewer"])

        self.github.pull_request(1, ref="moved", verdicts=[projector_verdict(SHA_A, "clean")])

        self.assertEqual([new_head(1, "moved", SHA_B, SHA_A, "reviewer")], self.cycle()["reviewer"])

    def test_a_merged_pull_request_tells_its_reviewer_to_finish(self) -> None:
        self.github.pull_request(1)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, state="MERGED")
        merged = self.cycle()["reviewer"]
        again = self.cycle()["reviewer"]

        self.assertEqual([f"MERGED acme/app#1 into main — have subagent review-1 run project review finish 1 --repo "
                          f"acme/app --model {MODEL} --force, then finish it"], merged)
        self.assertEqual([], again)
        self.assertEqual([], self.lines("reviewer"))

    def test_a_removed_subscription_is_dropped_without_a_word(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2)
        self.reviewer(1, 2)
        self.assertEqual(2, len(self.cycle()["reviewer"]))

        watch.remove(self.root, "reviewer", "acme/app", 2, "review")
        events = self.cycle()["reviewer"]

        self.assertEqual([], events)
        self.assertEqual([f"acme/app#1 review {MODEL}"], list(self.state("reviewer")["review"]))

    def test_carries_the_last_result_forward_when_a_query_fails(self) -> None:
        self.github.pull_request(1)
        self.reviewer(1)
        self.cycle()
        before = self.state("reviewer")

        self.github.pull_request(1, head=SHA_B)
        self.github.graphql_down = True
        failed = self.cycle()["reviewer"]
        during = self.state("reviewer")
        self.github.graphql_down = False
        recovered = self.cycle()["reviewer"]

        label = f"acme/app#1 review {MODEL}"
        self.assertEqual([], failed)
        self.assertEqual((SHA_A, SHA_A), (before["review"][label]["head"], during["review"][label]["head"]))
        self.assertEqual([new_head(1, "feature", SHA_A, SHA_B, "reviewer")], recovered)

    def test_a_clean_review_settles_a_pull_request_whoever_wrote_it(self) -> None:
        # A review moves no draft state, so a head with a clean newest verdict waits on nothing, whether the
        # operator or another author wrote the pull request.
        def both(**fields):
            self.github.pull_request(1, ref="mine", **fields)
            self.github.pull_request(2, ref="asked", author="teammate", **fields)

        both(requested=("operator",))
        self.subscribe("reviewer", f"acme/app review {MODEL} operator")
        self.renew("reviewer")
        adopted = self.cycle()["reviewer"]
        changes = projector_verdict(SHA_A)
        both(verdicts=[changes], threads=[thread("T1")])
        reviewed = self.cycle()["reviewer"]
        both(head=SHA_B, verdicts=[changes], threads=[thread("T1")])
        pushed = self.cycle()["reviewer"]
        under_review = self.cycle()["reviewer"]
        clean = projector_verdict(SHA_B, "clean", at="2026-10-12T11:00:00Z")
        both(head=SHA_B, verdicts=[changes, clean], threads=[thread("T1", resolved=True)])
        settled = self.cycle()["reviewer"]

        self.assertEqual(2, len(adopted), adopted)
        self.assertEqual([], reviewed)
        self.assertEqual(2, len(pushed), pushed)
        self.assertEqual([], under_review)
        self.assertEqual([], settled)

    def test_another_authors_answer_without_a_push_asks_for_a_re_review(self) -> None:
        # GitHub leaves reviewDecision null where the base branch requires no reviews, so the verdict marker is
        # what says the head waits on an answer.
        reply = {"databaseId": 12, "body": "<!-- projector-reply v=1 --> Declined: the caller checks for nil."}

        def teammates(threads, outsider_threads):
            changes = projector_verdict(SHA_A)
            self.github.pull_request(2, ref="draft", author="teammate", draft=True, verdicts=[changes],
                                     threads=threads)
            self.github.pull_request(3, ref="ready", author="teammate", verdicts=[changes], threads=outsider_threads)
            # Anyone can review a public repository, so a passerby's marker is no verdict there.
            # The reviewer's own clean verdict came first, so only the passerby's later one could ask for changes.
            mine = projector_verdict(SHA_A, "clean", at="2026-10-12T09:00:00Z")
            self.github.pull_request(4, ref="passerby", author="teammate", threads=outsider_threads,
                                     verdicts=[mine, projector_verdict(SHA_A, association="NONE", who="passerby")])
            # In a private one, every review counts.
            self.github.pull_request(5, repo="acme/secret", ref="private", author="teammate",
                                     threads=outsider_threads,
                                     verdicts=[mine, projector_verdict(SHA_A, association="NONE", who="passerby")])

        self.github.repository("acme/secret", visibility="PRIVATE")
        teammates([finding_thread()], [finding_thread()])
        self.reviewer(2, 3, 4)
        self.subscribe("reviewer", f"acme/secret#5 review {MODEL} operator")
        reviewed = self.cycle()["reviewer"]
        teammates([finding_thread(reply)], [finding_thread(resolved=True)])
        answered = self.cycle()["reviewer"]

        cross_author = "re-review this head, and a clean one is a COMMENT, never an approval"
        self.assertEqual([], reviewed)
        self.assertEqual([
            f"RESPONDED acme/app#2 (draft) head=aaaaaaa: changes requested and the author replied on an open finding; "
            f"{cross_author} — {rereview(2, 'reviewer')}",
            f"RESPONDED acme/app#3 (ready) head=aaaaaaa: changes requested with every thread resolved; {cross_author} "
            f"— {rereview(3, 'reviewer')}",
            f"RESPONDED acme/secret#5 (private) head=aaaaaaa: changes requested with every thread resolved; "
            f"{cross_author} — run review-pr on acme/secret#5 with --rereview in subagent review-5 with --loop "
            "reviewer",
        ], answered)

    def test_a_review_that_settles_findings_before_it_publishes_is_no_answer(self) -> None:
        changes = projector_verdict(SHA_A)

        def asked(head, verdicts, threads, decision="CHANGES_REQUESTED"):
            self.github.pull_request(2, ref="asked", author="teammate", head=head, decision=decision,
                                     verdicts=verdicts, threads=threads)

        asked(SHA_A, [changes], [thread("T1")])
        self.reviewer(2)
        self.assertEqual([], self.cycle()["reviewer"])

        asked(SHA_B, [changes], [thread("T1")])
        pushed = self.cycle()["reviewer"]
        under_review = self.cycle()["reviewer"]
        asked(SHA_B, [changes], [thread("T1", resolved=True)])
        settled = self.cycle()["reviewer"]
        clean = projector_verdict(SHA_B, "clean", at="2026-10-12T11:00:00Z")
        asked(SHA_B, [changes, clean], [thread("T1", resolved=True)], decision="REVIEW_REQUIRED")
        published = self.cycle()["reviewer"]

        self.assertEqual([new_head(2, "asked", SHA_A, SHA_B, "reviewer")], pushed)
        self.assertEqual(([], [], []), (under_review, settled, published))

    def test_an_unreviewed_head_is_announced_again_after_the_renotify_interval(self) -> None:
        self.github.pull_request(1)
        self.reviewer(1)
        self.cycle()

        self.advance(899)
        early = self.cycle()["reviewer"]
        self.advance(1)
        reminded = self.cycle()["reviewer"]
        self.lock("reviewer")
        self.advance(900)
        in_flight = self.cycle()["reviewer"]
        self.locks.clear()
        self.github.pull_request(1, verdicts=[projector_verdict(SHA_A, "clean")])
        self.advance(900)
        reviewed = self.cycle()["reviewer"]

        self.assertEqual([], early)
        self.assertEqual([f"NEW HEAD acme/app#1 (still open) (feature) head=aaaaaaa: still needs an exact-head review "
                          f"— {review_action(1, 'reviewer')}"], reminded)
        self.assertEqual([], in_flight)
        self.assertEqual([], reviewed)

    def test_a_live_start_comment_from_another_machine_is_a_review_in_flight(self) -> None:
        start = {"databaseId": 501, "author": {"login": "operator"}, "createdAt": "2026-10-30T00:00:00Z",
                 "body": f"📽️ Reviewing\n<!-- projector-start v=1 sha={SHA_A} model={MODEL} -->"}
        self.github.pull_request(1)
        self.reviewer(1)
        self.cycle()

        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock.wall() + 900))
        self.github.pull_request(1, comments=[dict(start, createdAt=at, updatedAt=at)])
        self.advance(900)
        elsewhere = self.cycle()["reviewer"]
        paths = review.Paths(self.review_root, "acme/app", 1, MODEL)
        paths.folder.mkdir(parents=True)
        paths.state.write_text(json.dumps({"start_comment": {"id": 501}}))
        self.advance(900)
        recorded_here = self.cycle()["reviewer"]

        self.assertEqual([], elsewhere)
        self.assertEqual(1, len(recorded_here), recorded_here)
        self.assertTrue(recorded_here[0].startswith("NEW HEAD acme/app#1 (still open)"), recorded_here)


class ConditionalRequestTests(WatchCase):
    def fixer(self, *numbers: int) -> None:
        self.subscribe("fixer", *(f"acme/app#{number} fix" for number in numbers))
        self.renew("fixer")

    def test_a_pass_whose_requests_answer_304_makes_no_graphql_call(self) -> None:
        self.github.pull_request(1)
        self.fixer(1)

        self.cycle()
        first = self.github.queried()
        self.advance(60)
        self.cycle()

        self.assertEqual(["acme/app#1"], first)
        self.assertEqual([], self.github.queried())
        self.assertIn(["api", "-i", "-H", self.github.calls[0][3], "repos/acme/app/pulls/1"], self.github.calls)
        self.assertTrue(self.github.calls[0][3].startswith('If-None-Match: W/"'), self.github.calls[0])

    def test_a_200_queries_only_that_pull_request(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2)
        self.fixer(1, 2)
        self.cycle()

        self.github.pull_request(2, threads=[thread("T2")])
        events = self.cycle()["fixer"]

        self.assertEqual(["acme/app#2"], self.github.queried())
        self.assertEqual(1, len(events), events)

    def test_a_new_review_is_seen_through_the_reviews_page(self) -> None:
        self.github.pull_request(1)
        self.fixer(1)
        self.cycle()

        self.github.repository()["pulls"][1]["reviews"].append(rest_review(7, body="Body only"))
        events = self.cycle()["fixer"]

        self.assertEqual(["acme/app#1"], self.github.queried())
        self.assertEqual([f"REVIEW acme/app#1 by [codex] on aaaaaaaa: Body only — {fix_action(1)}"], events)

    def test_a_failed_request_queries_and_a_failed_query_carries_the_result_forward(self) -> None:
        self.github.pull_request(1, threads=[thread("T1")])
        self.fixer(1)
        self.cycle()
        cached = read_json(watch.pull_cache(self.root, "acme/app", 1))

        for status in (500, 403, None):
            with self.subTest(status=status):
                self.github.failing["repos/acme/app/pulls/1"] = status
                self.advance(60)
                self.cycle()
                self.assertEqual(["acme/app#1"], self.github.queried())

        self.github.graphql_down = True
        self.advance(60)
        events = self.cycle()["fixer"]

        self.assertEqual(["acme/app#1"], self.github.queried())
        self.assertEqual([], events)
        self.assertEqual(cached["repository"], read_json(watch.pull_cache(self.root, "acme/app", 1))["repository"])

    def test_a_change_seen_while_the_query_fails_is_queried_again_next_pass(self) -> None:
        self.github.pull_request(1)
        self.fixer(1)
        self.cycle()

        self.github.pull_request(1, threads=[thread("T1")])
        self.github.graphql_down = True
        failed = self.cycle()["fixer"]
        self.github.graphql_down = False
        recovered = self.cycle()["fixer"]

        self.assertEqual([], failed)
        self.assertEqual(1, len(recovered), recovered)

    def test_every_target_gets_one_query_each_full_pass(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2)
        self.fixer(1, 2)
        self.cycle()

        self.advance(300)
        self.cycle()
        quiet = self.github.queried()
        self.advance(300)
        self.cycle()
        full = self.github.queried()

        self.assertEqual([], quiet)
        self.assertEqual(["acme/app#1", "acme/app#2"], full)
        self.assertEqual(2, sum("reviews?per_page=100" in " ".join(call) and "--paginate" in call
                                for call in self.github.calls))

    def test_a_moved_base_queries_only_the_pull_request_a_session_fixes(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2)
        self.fixer(1)
        self.subscribe("reviewer", f"acme/app#2 review {MODEL} operator")
        self.renew("reviewer")
        self.cycle()
        self.advance(60)
        self.cycle()
        self.assertEqual([], self.github.queried())

        self.github.repository()["branches"]["main"] = SHA_B
        self.advance(60)
        self.cycle()

        self.assertEqual(["acme/app#1"], self.github.queried())
        self.assertEqual(1, sum(call[-1] == "repos/acme/app/git/ref/heads/main" for call in self.github.calls))

    def test_a_deleted_base_queries_the_pull_request_once(self) -> None:
        self.github.pull_request(1, base="parent")
        self.github.repository()["branches"]["parent"] = SHA_C
        self.fixer(1)
        self.cycle()

        del self.github.repository()["branches"]["parent"]
        self.advance(60)
        self.cycle()
        once = self.github.queried()
        self.advance(60)
        self.cycle()

        self.assertEqual(["acme/app#1"], once)
        self.assertEqual([], self.github.queried())

    def test_a_new_operator_drops_every_etag(self) -> None:
        self.github.pull_request(1)
        self.fixer(1)
        self.cycle()

        self.github.operator = "other"
        self.advance(600)
        self.cycle()

        self.assertEqual("other", read_json(self.root / "daemon.json")["operator"])
        self.assertNotIn("-H", [arg for call in self.github.calls for arg in call])


class OwnerTests(WatchCase):
    def subscribe_live(self, session: str, *lines: str) -> None:
        self.subscribe(session, *lines)
        self.renew(session)

    def test_a_change_is_queried_once_and_each_role_hears_its_own_event(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("fixer", "acme/app#1 fix")
        self.subscribe_live("reviewer", f"acme/app#1 review {MODEL} operator")
        self.cycle()

        self.github.pull_request(1, head=SHA_B, threads=[thread("T1")])
        events = self.cycle()

        self.assertEqual(["acme/app#1"], self.github.queried())
        self.assertEqual([f"FINDING acme/app#1 src/x.py:3 [bugbot]: P1 nil deref — {fix_action(1)}"], events["fixer"])
        self.assertEqual([new_head(1, "feature", SHA_A, SHA_B, "reviewer")], events["reviewer"])

    def test_a_pull_requests_subscription_outranks_its_repositorys(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2)
        self.subscribe_live("A", f"acme/app review {MODEL} operator")
        first = self.cycle()

        self.subscribe_live("B", f"acme/app#2 review {MODEL} operator")
        moved = self.cycle()

        self.assertEqual([new_pr(1, "feature", "A"), new_pr(2, "feature", "A")], first["A"])
        self.assertEqual([f"MOVED acme/app#2 review for {MODEL} to another session — stop subagent review-2 and run "
                          "no project review finish"], moved["A"])
        self.assertEqual([new_pr(2, "feature", "B")], moved["B"])
        self.assertEqual({f"acme/app#1 review {MODEL}": "A", f"acme/app#2 review {MODEL}": "B"}, self.owners())
        self.assertEqual([("A", "B", "acme/app", 2)], self.copies)
        self.assertEqual({"acme/app": [1, 2]}, self.state("A")["adopted"])

    def test_a_lapsed_owner_gives_the_role_back_and_its_successor_hears_only_an_unreviewed_head(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(2, verdicts=[projector_verdict(SHA_A, "clean")])
        self.subscribe_live("A", f"acme/app review {MODEL} operator")
        self.cycle()
        self.subscribe_live("B", f"acme/app#1 review {MODEL} operator", f"acme/app#2 review {MODEL} operator")
        self.cycle()

        self.lapse("B")
        self.advance(301)
        events = self.cycle()

        self.assertEqual([new_pr(1, "feature", "A")], events["A"])
        self.assertEqual([], events["B"])
        self.assertEqual({f"acme/app#1 review {MODEL}": "A", f"acme/app#2 review {MODEL}": "A"}, self.owners())

    def test_a_new_fix_owner_hears_of_labels_only_once_it_has_seen_them(self) -> None:
        self.github.pull_request(1, labels=["clean"])
        self.subscribe_live("A", "acme/app#1 fix")
        self.cycle()
        self.subscribe_live("B", "acme/app#1 fix")
        self.cycle()

        self.lapse("A")
        self.advance(301)
        moved = self.cycle()
        self.github.pull_request(1)
        unlabeled = self.cycle()

        self.assertEqual({"acme/app#1 fix": "B"}, self.owners())
        self.assertEqual([], [line for line in moved["B"] if line.startswith("LABEL")])
        self.assertEqual([label("-clean")], [line for line in unlabeled["B"] if line.startswith("LABEL")])
        self.assertEqual({}, self.state("A")["labels"])

    def test_the_role_waits_for_a_review_in_flight_then_moves_with_its_record(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.cycle()
        lock = self.lock("A")

        self.subscribe_live("B", f"acme/app#1 review {MODEL} operator")
        waiting = self.cycle()
        self.clear_lock(lock)
        self.github.pull_request(1, verdicts=[projector_verdict(SHA_A, "clean")])
        published = self.cycle()

        self.assertEqual(([], []), (waiting["A"], waiting["B"]))
        self.assertEqual([f"MOVED acme/app#1 review for {MODEL} to another session — stop subagent review-1 and run "
                          "no project review finish"], published["A"])
        self.assertEqual([], published["B"])
        self.assertEqual({f"acme/app#1 review {MODEL}": "B"}, self.owners())
        self.assertEqual([("A", "B", "acme/app", 1)], self.copies)

    def test_a_stalled_review_moves_to_the_session_waiting_for_it(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.cycle()
        lock = self.lock("A")
        self.subscribe_live("B", f"acme/app#1 review {MODEL} operator")
        self.cycle()

        self.advance(1799)
        before = self.cycle()
        self.advance(1)
        stalled = self.cycle()

        self.assertEqual(([], []), (before["A"], before["B"]))
        self.assertEqual([lock], self.cleared)
        self.assertTrue(stalled["A"][0].startswith("MOVED acme/app#1 review"), stalled["A"])
        self.assertEqual([new_pr(1, "feature", "B")], stalled["B"])

    def test_a_stalled_review_with_no_other_candidate_is_announced_again(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.cycle()
        lock = self.lock("A")
        self.cycle()

        self.advance(1000)
        self.touch(lock)
        touched = self.cycle()
        self.advance(1799)
        before = self.cycle()
        self.advance(1)
        stalled = self.cycle()

        self.assertEqual(([], []), (touched["A"], before["A"]))
        self.assertEqual([lock.path], [found.path for found in self.cleared])
        self.assertEqual(["NEW HEAD acme/app#1 (feature) head=aaaaaaa, its last review stalled — stop subagent "
                          f"review-1, then {review_action(1, 'A')}"], stalled["A"])

    def test_a_review_that_stalls_on_a_draft_still_tells_its_subagent_to_stop(self) -> None:
        # A stall is reported on the pass that finds it, so the wait for a draft must not swallow it.
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app review {MODEL} operator")
        self.cycle()
        self.lock("A")
        self.github.pull_request(1, draft=True)
        self.cycle()

        self.advance(1800)
        stalled = self.cycle()
        self.advance(60)
        quiet = self.cycle()
        self.github.pull_request(1)
        ready = self.cycle()

        self.assertEqual(["NEW HEAD acme/app#1 (feature) head=aaaaaaa, its last review stalled while the pull request "
                          "is a draft — stop subagent review-1, and review it once it is ready"], stalled["A"])
        self.assertEqual([], quiet["A"])
        self.assertEqual([f"NEW HEAD acme/app#1 (still open) (feature) head=aaaaaaa: still needs an exact-head review "
                          f"— {review_action(1, 'A')}"], ready["A"], "the head no review read is announced at once")

    def test_a_lapsed_owner_loses_its_locks_at_once(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("B", f"acme/app review {MODEL} operator")
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.cycle()
        lock = self.lock("A")
        self.cycle()

        self.lapse("A")
        self.advance(301)
        events = self.cycle()

        self.assertEqual([lock], self.cleared)
        self.assertEqual([new_pr(1, "feature", "B")], events["B"])
        self.assertEqual([("A", "B", "acme/app", 1)], self.copies)

    def test_a_machine_that_sleeps_lapses_no_session_and_stalls_no_review(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.daemon.tick()
        self.lock("A")
        self.subscribe_live("B", f"acme/app#1 review {MODEL} operator")
        self.daemon.tick()
        self.take("A")
        self.take("B")

        self.clock.advance(3600)
        self.assertTrue(self.daemon.tick())

        self.assertEqual({"A", "B"}, self.daemon.live)
        self.assertEqual([], self.cleared)
        self.assertEqual(([], []), (self.take("A"), self.take("B")))
        self.assertEqual({f"acme/app#1 review {MODEL}": "A"}, self.owners())

    def test_a_pass_that_outlasts_a_minute_is_no_sleep_so_a_review_still_stalls(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.daemon.tick()
        lock = self.lock("A")
        self.daemon.tick()
        self.take("A")

        # Every call to GitHub takes 61 seconds, so every pass outlasts SLEPT, as a busy daemon's passes do.
        github = self.github

        def slow(args: list[str]) -> watch.GhResult:
            self.advance(61)
            return github(args)

        # The review stalls at 30 minutes, and the pass that finds it ends a few calls later.
        self.daemon.gh = slow
        start = self.clock.mono()
        while self.clock.mono() - start < 2400 and not self.cleared:
            self.advance(1)
            self.assertTrue(self.daemon.tick())

        self.assertEqual([lock.path], [found.path for found in self.cleared])

    def test_after_a_move_the_new_owner_hears_the_authors_answer(self) -> None:
        self.github.pull_request(1, draft=True, verdicts=[projector_verdict(SHA_A)], threads=[finding_thread()])
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.cycle()
        self.subscribe_live("B", f"acme/app#1 review {MODEL} operator")
        moved = self.cycle()

        self.github.pull_request(1, draft=True, verdicts=[projector_verdict(SHA_A)],
                                 threads=[finding_thread(resolved=True)])
        answered = self.cycle()

        self.assertEqual([], moved["B"])
        self.assertEqual([responded("B")], answered["B"])
        self.assertEqual([], answered["A"])

    def test_two_models_each_review(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", f"acme/app#1 review {MODEL} operator")
        self.subscribe_live("B", "acme/app#1 review gpt-6 operator")
        first = self.cycle()

        self.github.pull_request(1, head=SHA_B)
        pushed = self.cycle()

        self.assertEqual(([new_pr(1, "feature", "A")], [new_pr(1, "feature", "B")]), (first["A"], first["B"]))
        self.assertEqual(([new_head(1, "feature", SHA_A, SHA_B, "A")], [new_head(1, "feature", SHA_A, SHA_B, "B")]),
                         (pushed["A"], pushed["B"]))

    def test_an_exclusion_keeps_a_session_from_hearing_a_pull_request(self) -> None:
        self.github.pull_request(1)
        self.github.pull_request(15)
        self.subscribe_live("A", f"acme/app review {MODEL} operator", f"!acme/app#15 review {MODEL} operator")

        events = self.cycle()

        self.assertEqual([new_pr(1, "feature", "A")], events["A"])
        self.assertEqual({"acme/app": [1]}, self.state("A")["adopted"])
        self.assertNotIn("pulls/15", self.rest_paths())

    def test_a_merge_reaches_each_owner_and_ends_every_sessions_lines(self) -> None:
        self.github.pull_request(1)
        self.subscribe("lapsed", "acme/app#1 fix")
        self.subscribe_live("fixer", "acme/app#1 fix")
        self.subscribe_live("reviewer", f"acme/app#1 review {MODEL} operator")
        self.cycle()

        self.github.pull_request(1, state="MERGED")
        events = self.cycle()

        self.assertEqual(["MERGED acme/app#1 into main — finish subagent fix-1"], events["fixer"])
        self.assertEqual([f"MERGED acme/app#1 into main — have subagent review-1 run project review finish 1 --repo "
                          f"acme/app --model {MODEL} --force, then finish it"], events["reviewer"])
        self.assertEqual(([], [], []), (self.lines("lapsed"), self.lines("fixer"), self.lines("reviewer")))
        self.assertEqual({}, self.owners())

    def test_a_pushed_fix_to_another_sessions_pull_request_takes_no_role(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", "acme/app#1 fix")
        self.cycle()
        self.subscribe_live("B", "acme/app#1 fix auto")
        auto = self.cycle()

        watch.add(self.root, "B", watch.Subscription("acme/app", 1, "fix"))
        self.order += 1
        os.utime(self.folder("B") / "subscriptions", (self.clock.wall() + 99, self.clock.wall() + 99))
        explicit = self.cycle()

        self.assertEqual(([], []), (auto["A"], auto["B"]))
        self.assertEqual(["acme/app#1 fix"], self.lines("B"))
        self.assertEqual(["MOVED acme/app#1 fix to another session — stop subagent fix-1"], explicit["A"])
        self.assertEqual({"acme/app#1 fix": "B"}, self.owners())

    def test_the_older_of_two_auto_lines_keeps_the_role(self) -> None:
        self.github.pull_request(1)
        self.subscribe_live("A", "acme/app#1 fix auto")
        self.subscribe_live("B", "acme/app#1 fix auto")

        self.cycle()

        self.assertEqual({"acme/app#1 fix": "A"}, self.owners())


class AdoptionTests(WatchCase):
    def repository_reviewer(self, *extra: str) -> None:
        self.subscribe("reviewer", f"acme/app review {MODEL} operator", *extra)
        self.renew("reviewer")

    def test_adopts_the_operators_pull_requests_and_those_requesting_the_operator_by_login(self) -> None:
        self.github.pull_request(1, ref="mine")
        self.github.pull_request(2, ref="asked", author="teammate", requested=("ada", "operator"))
        self.github.pull_request(3, ref="asked-elsewhere", author="teammate", requested=("ada",))
        self.github.pull_request(4, ref="merged", author="operator", state="MERGED")
        self.github.pull_request(5, ref="team", author="teammate", teams=("operator",))
        self.repository_reviewer()

        first = self.cycle()["reviewer"]
        # The operator reviewed, which drops the request, and the author pushed.
        self.github.pull_request(2, ref="asked", author="teammate", head=SHA_B)
        after_review = self.cycle()["reviewer"]

        self.assertEqual([new_pr(1, "mine", "reviewer"), new_pr(2, "asked", "reviewer")], first)
        self.assertEqual([new_head(2, "asked", SHA_A, SHA_B, "reviewer")], after_review)
        self.assertEqual({"acme/app": [1, 2]}, self.state("reviewer")["adopted"])

    def test_a_listing_that_has_not_changed_answers_304_and_still_covers_its_pull_requests(self) -> None:
        self.github.pull_request(1)
        self.repository_reviewer()
        self.cycle()

        self.advance(60)
        events = self.cycle()["reviewer"]

        self.assertEqual([], events)
        self.assertIn("If-None-Match", " ".join(next(call for call in self.github.calls if "pulls?state=open" in
                                                     call[-1])))
        self.assertEqual({f"acme/app#1 review {MODEL}": "reviewer"}, self.owners())

    def test_a_failed_listing_adopts_nothing_that_pass(self) -> None:
        self.github.pull_request(1)
        self.repository_reviewer()
        self.github.failing["repos/acme/app/pulls"] = 500

        failed = self.cycle()["reviewer"]
        del self.github.failing["repos/acme/app/pulls"]
        recovered = self.cycle()["reviewer"]

        self.assertEqual([], failed)
        self.assertEqual([new_pr(1, "feature", "reviewer")], recovered)

    def test_a_repository_that_does_not_exist_is_missing_once(self) -> None:
        self.subscribe("reviewer", f"acme/gone review {MODEL} operator", f"acme/app review {MODEL} operator")
        self.renew("reviewer")
        self.github.repository()

        events = self.cycle()["reviewer"]

        self.assertEqual(["MISSING acme/gone: no such repository, or the operator cannot see it, so the watch removed "
                          "its subscription — tell the user"], events)
        self.assertEqual([f"acme/app review {MODEL} operator"], self.lines("reviewer"))


class LivenessTests(WatchCase):
    def test_a_lapsed_session_is_not_polled_and_hears_what_is_outstanding_when_it_returns(self) -> None:
        self.github.pull_request(1, threads=[thread("T1")])
        self.subscribe("fixer", "acme/app#1 fix")
        self.renew("fixer")
        first = self.cycle()["fixer"]

        self.lapse("fixer")
        self.advance(301)
        lapsed = self.cycle()["fixer"]
        lapsed_calls = list(self.github.calls)
        self.renew("fixer")
        returned = self.cycle()["fixer"]

        self.assertEqual(first, returned)
        self.assertEqual([], lapsed)
        self.assertEqual([], lapsed_calls)

    def test_a_new_session_or_a_new_subscription_without_a_result_asks_for_a_pass_at_once(self) -> None:
        self.github.pull_request(1)
        self.daemon.tick()
        self.assertFalse(self.daemon.wanted)

        self.renew("fixer")
        self.clock.advance(1)
        self.daemon.scan(self.clock.mono())
        renewed = self.daemon.wanted
        self.daemon.run_pass()
        self.subscribe("fixer", "acme/app#1 fix")
        self.daemon.scan(self.clock.mono())

        self.assertTrue(renewed)
        self.assertTrue(self.daemon.wanted)

    def test_a_session_folder_lapsed_for_a_week_is_deleted(self) -> None:
        self.subscribe("old", "acme/app#1 fix")
        week = time.time() - watch.FORGET - 60
        for path in self.folder("old").iterdir():
            os.utime(path, (week, week))

        self.cycle()

        self.assertFalse(self.folder("old").exists())

    def serve(self, sleep) -> tuple[int, str]:
        log = io.StringIO()
        with redirect_stdout(log):
            status = watch.serve(self.root, "1.0", sleep=sleep, gh=self.github, wall=self.clock.wall,
                                 mono=self.clock.mono, settings=self.settings, review_root=self.review_root)
        return status, log.getvalue()

    def test_the_daemon_exits_five_minutes_after_the_last_session_lapses(self) -> None:
        self.renew("fixer")
        sleeps = []

        def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            self.clock.advance(10)

        status, log = self.serve(sleep)

        # The session lapses 300 seconds after its renewal, at 1300. The last turn that saw it live was at 1290,
        # and the daemon exits on the first turn 300 seconds after that.
        self.assertEqual(0, status)
        self.assertIn("stopping: no session has been live for 5 minutes", log)
        self.assertEqual(1590, self.clock.mono())
        self.assertEqual(59, len(sleeps))
        self.assertEqual({"pid": os.getpid(), "version": "1.0", "operator": "operator"},
                         {key: read_json(self.root / "daemon.json")[key] for key in ("pid", "version", "operator")})

    def test_a_stop_file_naming_the_daemon_stops_it(self) -> None:
        self.renew("fixer")

        def sleep(seconds: float) -> None:
            self.clock.advance(1)
            self.renew("fixer")
            if self.clock.mono() >= 1003:
                watch.write_text(self.root / "stop", f"{os.getpid()}\n")

        status, log = self.serve(sleep)

        self.assertEqual(0, status)
        self.assertIn("stopping: the stop file names this daemon", log)
        self.assertEqual(1003, self.clock.mono())
        self.assertFalse((self.root / "stop").exists())
        self.assertFalse(watch.held(self.root))

    def test_a_second_daemon_finds_the_lock_held_and_exits(self) -> None:
        self.root.mkdir(parents=True)
        descriptor = os.open(self.root / "daemon.mutex", os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(os.close, descriptor)
        self.assertTrue(try_lock(descriptor))
        self.addCleanup(unlock, descriptor)

        status, log = self.serve(self.fail)

        self.assertEqual(0, status)
        self.assertIn("another daemon holds", log)
        self.assertFalse((self.root / "daemon.json").exists())
        self.assertEqual([], self.github.calls)


class StartTests(WatchCase):
    """Every command but `serve` starts the daemon, and replaces only a strictly older one."""

    def hold(self, version: object, pid: int = 777) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        watch.write_json(self.root / "daemon.json", {"pid": pid, "version": version})
        descriptor = os.open(self.root / "daemon.mutex", os.O_RDWR | os.O_CREAT, 0o600)
        self.assertTrue(try_lock(descriptor))
        self.descriptor = descriptor
        self.addCleanup(self.release)
        return descriptor

    def release(self) -> None:
        if self.descriptor is not None:
            unlock(self.descriptor)
            os.close(self.descriptor)
            self.descriptor = None

    def ensure(self, own: str) -> tuple[str, list[Path]]:
        spawned: list[Path] = []

        def sleep(seconds: float) -> None:
            if (self.root / "stop").exists() and self.descriptor is not None:
                self.release()

        result = watch.ensure_daemon(self.root, own, cli.newer_release, spawn=spawned.append, sleep=sleep)
        return result, spawned

    def setUp(self) -> None:
        super().setUp()
        self.descriptor = None

    def test_a_command_with_no_daemon_running_starts_one(self) -> None:
        self.assertEqual(("started", [self.root]), self.ensure("0.9.0"))

    def test_a_newer_command_replaces_an_older_daemon(self) -> None:
        self.hold("0.8.5")

        result, spawned = self.ensure("0.8.6")

        self.assertEqual(("replaced", [self.root]), (result, spawned))
        self.assertEqual("777", (self.root / "stop").read_text().strip())

    def test_no_daemon_is_replaced_by_its_own_version_an_older_one_or_across_unknown(self) -> None:
        for running, own in (("0.8.6", "0.8.6"), ("0.8.6", "0.8.5"), ("0.8.5", "unknown"), ("unknown", "0.8.6")):
            with self.subTest(running=running, own=own):
                self.hold(running)

                result, spawned = self.ensure(own)

                self.assertEqual(("running", []), (result, spawned))
                self.assertFalse((self.root / "stop").exists())
                self.release()

    def test_stop_asks_the_daemon_to_exit_and_waits_for_its_lock(self) -> None:
        self.hold("0.8.6", pid=4321)

        def sleep(seconds: float) -> None:
            if (self.root / "stop").exists():
                self.release()

        self.assertEqual(4321, watch.stop_daemon(self.root, sleep=sleep))
        self.assertIsNone(watch.stop_daemon(self.root))


@unittest.skipUnless(os.name == "posix", "the detached daemon test starts a POSIX shell script as gh")
class DetachedDaemonTests(unittest.TestCase):
    """`project watch` starts a real, detached `serve`, which answers only to the fake `gh` on PATH."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        bin_dir = self.scratch / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text('#!/bin/sh\n[ "$1 $2" = "api user" ] && { echo \'{"login":"operator"}\'; exit 0; }\n'
                        'echo "fake gh: unsupported: $*" >&2\nexit 3\n')
        fake.chmod(0o755)
        package = str(Path(watch.__file__).resolve().parents[1])
        environ = mock.patch.dict(os.environ, {
            "XDG_STATE_HOME": str(self.scratch / "state"), "HOME": str(self.scratch),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "PYTHONPATH": os.pathsep.join(filter(None, [package, os.environ.get("PYTHONPATH")])),
        })
        environ.start()
        self.addCleanup(environ.stop)
        self.root = watch.state_root()
        self.processes: list[subprocess.Popen] = []
        spawn = watch.spawn_daemon

        def spawned(root: Path) -> subprocess.Popen:
            process = spawn(root)
            self.processes.append(process)
            return process

        patcher = mock.patch.object(watch, "spawn_daemon", spawned)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.reap)

    def reap(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

    def wait_for(self, condition, seconds: float = 20.0) -> None:
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                log = self.root / "daemon.log"
                self.fail(f"timed out; daemon.log: {log.read_text() if log.exists() else '(none)'}")
            time.sleep(0.05)

    def daemon_pid(self) -> Optional[int]:
        return (read_json(self.root / "daemon.json") or {}).get("pid")

    def test_a_command_starts_a_detached_daemon_and_a_killed_one_is_started_again(self) -> None:
        self.assertEqual("started", watch.ensure_daemon(self.root, "1.0", cli.newer_release))
        self.wait_for(lambda: watch.held(self.root) and self.daemon_pid())
        first = self.daemon_pid()
        self.addCleanup(lambda: watch.held(self.root) and watch.stop_daemon(self.root))
        # The daemon records the version it finds installed, which build metadata left in `src/` changes, so the
        # command asks as that same version and never as a newer one.
        recorded = str((read_json(self.root / "daemon.json") or {}).get("version"))

        again = watch.ensure_daemon(self.root, recorded, cli.newer_release)
        os.kill(first, signal.SIGKILL)
        self.wait_for(lambda: not watch.held(self.root))
        restarted = watch.ensure_daemon(self.root, recorded, cli.newer_release)
        self.wait_for(lambda: watch.held(self.root) and self.daemon_pid() not in (None, first))
        second = self.daemon_pid()
        stopped = watch.stop_daemon(self.root)

        self.assertEqual(("running", "started"), (again, restarted))
        self.assertEqual(second, stopped)
        self.assertFalse(watch.held(self.root))


class OutputTests(unittest.TestCase):
    def test_reads_pull_requests_and_pushed_branches_from_a_commands_output(self) -> None:
        output = ("remote: Create a pull request for 'fresh' on GitHub by visiting:\n"
                  "remote:      https://github.com/acme/app/pull/new/fresh\n"
                  "To github.com:acme/app.git\n"
                  "   1a2b3c4..5d6e7f8  feature -> feature\n"
                  " + 9a8b7c6...1f2e3d4 other -> refs/heads/other (forced update)\n"
                  " * [new branch]      fresh -> fresh\n"
                  "To https://gitlab.com/acme/app.git\n"
                  "   1a2b3c4..5d6e7f8  mirrored -> mirrored\n"
                  "https://github.com/acme/web/pull/13\n"
                  "Creating draft pull request for fresh into main in Acme/Web\n")

        self.assertEqual(([("acme/web", 13)], [("acme/app", "feature"), ("acme/app", "other")], {"acme/app", "acme/web"}),
                         watch.pushed(output))

    def test_reads_the_status_line_and_etag_that_gh_api_i_prints(self) -> None:
        printed = 'HTTP/2.0 200 OK\r\nEtag: W/"abc"\r\nX-Ratelimit-Used: 3\r\n\r\n{"number": 1}'

        response = watch.parse_response(printed)

        self.assertEqual((200, 'W/"abc"', {"number": 1}), (response.status, response.etag, response.json()))
        self.assertEqual((None, None), (watch.parse_response("").status, watch.parse_response("").etag))
        self.assertEqual(304, watch.parse_response('HTTP/2.0 304 Not Modified\nEtag: "abc"\n\n').status)

    def test_parses_each_subscription_line_and_refuses_the_rest(self) -> None:
        parse = watch.Subscription.parse
        self.assertEqual(watch.Subscription("acme/app", 13, "fix", auto=True), parse("acme/app#13 fix auto"))
        self.assertEqual(watch.Subscription("acme/app", None, "review", MODEL, "octocat"),
                         parse(f"acme/app review {MODEL} octocat"))
        self.assertEqual(watch.Subscription("acme/app", 9, "review", MODEL, "octocat", excluded=True),
                         parse(f"!acme/app#9 review {MODEL} octocat"))
        for refused in ("acme/app fix", "!acme/app#9 fix auto", f"!acme/app review {MODEL} octocat", "acme/app#9",
                        "acme/app#9 review", "2013"):
            with self.subTest(line=refused):
                self.assertIsNone(parse(refused))


class CommandTests(unittest.TestCase):
    """`project watch` from the command line, with the daemon's start stood in for."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.cwd = self.scratch / "work"
        self.cwd.mkdir()
        environ = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.scratch / "state"),
                                               "HOME": str(self.home), "PROJECTOR_OFFLINE": "1",
                                               "CLAUDE_CODE_SESSION_ID": "top-level"})
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("PROJECTOR_SESSION", None)
        self.spawned: list[Path] = []
        spawn = mock.patch.object(watch, "spawn_daemon", self.spawned.append)
        spawn.start()
        self.addCleanup(spawn.stop)
        self.github = FakeGitHub()
        gh = mock.patch.object(watch, "run_gh", self.github)
        gh.start()
        self.addCleanup(gh.stop)
        self.root = watch.state_root()

    def project(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.chdir(self.cwd), redirect_stdout(out), redirect_stderr(err), \
                mock.patch.object(sys, "stdin", io.StringIO(stdin)):
            code = cli.main(["watch", *args])
        return code, out.getvalue(), err.getvalue()

    def lines(self, session: str = "top-level") -> list[str]:
        return watch.read_lines(self.root / "sessions" / session)

    def origin(self, url: str) -> None:
        for args in (["init", "-q"], ["remote", "add", "origin", url]):
            subprocess.run(["git", *args], cwd=self.cwd, check=True, capture_output=True)

    def test_add_starts_the_daemon_and_says_how_events_arrive(self) -> None:
        code, out, err = self.project("add", "acme/app#12")

        self.assertEqual(0, code, err)
        self.assertEqual("added acme/app#12 fix\nevents: none — start `project watch stream --session top-level` "
                         "under your host's monitor facility\n", out)
        self.assertEqual([self.root], self.spawned)
        self.assertEqual(["acme/app#12 fix"], self.lines())

    def test_add_names_the_deliverer_that_renews_the_session(self) -> None:
        self.assertEqual(0, self.project("renew", "--deliverer", "mod")[0])

        code, out, err = self.project("add", "https://github.com/acme/app/pull/12/files", "--json")

        self.assertEqual(0, code, err)
        self.assertEqual({"added": ["acme/app#12 fix"], "delivery": "mod", "codex_hooks": None, "schema_version": 2},
                         json.loads(out))

    def fake_codex(self, status: str) -> Path:
        """A `codex` whose `app-server` lists Projector's five hooks with `status`."""
        executable = self.scratch / "codex"
        hooks = [{"eventName": name, "command": "project watch codex-hook", "enabled": True, "trustStatus": status}
                 for name in ("sessionStart", "sessionEnd", "userPromptSubmit", "postToolUse", "stop")]
        executable.write_text(f"#!{sys.executable}\nimport json, sys\nhooks = {hooks!r}\n" + '''
for line in sys.stdin:
    message = json.loads(line)
    if message.get("id") == 1:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif message.get("id") == 2:
        print(json.dumps({"id": 2, "result": {"data": [{"hooks": hooks}]}}), flush=True)
''')
        executable.chmod(0o755)
        return executable

    def test_add_reports_codex_hooks_codex_has_stopped_running(self) -> None:
        folder = watch.session_folder(self.root, "codex-A")
        folder.mkdir(parents=True)
        watch.write_json(folder / "codex.json", {"schema_version": 1, "thread_id": "codex-A", "enabled": True,
                                           "executable": str(self.fake_codex("modified")), "cwd": str(self.cwd),
                                           "expires_at": time.time() + 3600})

        code, out, err = self.project("add", "acme/app#12", "--session", "codex-A")

        self.assertEqual(0, code, err)
        self.assertEqual("added acme/app#12 fix\nevents: codex — but Codex is not running Projector's hooks "
                         "(modified); trust them in /hooks\n", out)

    def test_add_reports_untrusted_hooks_for_a_codex_session_they_never_registered(self) -> None:
        executable = self.fake_codex("untrusted")
        with mock.patch.dict(os.environ, {"CODEX_SESSION_ID": "codex-B"}), \
                mock.patch.object(shutil, "which", lambda name: str(executable) if name == "codex" else None):
            code, out, err = self.project("add", "acme/app#12", "--session", "codex-B", "--json")
            _, trusted, _ = self.project("add", "acme/app#13", "--session", "top-level", "--json")

        self.assertEqual(0, code, err)
        self.assertEqual("Codex is not running Projector's hooks (untrusted); trust them in /hooks",
                         json.loads(out)["codex_hooks"])
        self.assertIsNone(json.loads(trusted)["codex_hooks"])

    def test_add_lands_in_the_session_the_host_names(self) -> None:
        self.project("add", "acme/app#12")
        self.project("add", "acme/app#13", "--session", "other")

        self.assertEqual((["acme/app#12 fix"], ["acme/app#13 fix"]), (self.lines(), self.lines("other")))

    def test_add_review_needs_a_model_and_resolves_the_reviewer(self) -> None:
        refused = self.project("add", "acme/app#14", "--role", "review")
        explicit = self.project("add", "acme/app#14", "--role", "review", "--model", MODEL, "--reviewer", "octocat")
        (self.home / ".projector.toml").write_text('[review]\nusername = "botty"\n')
        configured = self.project("add", "acme/app", "--role", "review", "--model", MODEL)
        repository_fix = self.project("add", "acme/app")

        self.assertEqual(2, refused[0])
        self.assertIn("--role review needs --model", refused[2])
        self.assertEqual(0, explicit[0], explicit[2])
        self.assertEqual(0, configured[0], configured[2])
        self.assertEqual(2, repository_fix[0])
        self.assertEqual([f"acme/app#14 review {MODEL} octocat", f"acme/app review {MODEL} botty"], self.lines())
        self.assertEqual([], self.github.calls)

    def test_add_with_no_reviewer_configured_posts_as_the_operator(self) -> None:
        with mock.patch.object(review, "authenticated_login", return_value="operator"):
            code, out, err = self.project("add", "acme/app#14", "--role", "review", "--model", MODEL)

        self.assertEqual(0, code, err)
        self.assertEqual([f"acme/app#14 review {MODEL} operator"], self.lines())

    def test_add_refuses_what_the_session_drops_and_an_explicit_add_drops_the_auto_mark(self) -> None:
        watch.edit_subscriptions(self.root / "sessions" / "top-level",
                                 lambda lines: ["acme/app#12 fix auto", "!acme/app#13 fix"])

        explicit = self.project("add", "acme/app#12")
        dropped = self.project("add", "acme/app#13")
        again = self.project("add", "acme/app#12")

        self.assertEqual(0, explicit[0], explicit[2])
        self.assertEqual(65, dropped[0])
        self.assertIn("project watch restore acme/app#13 --role fix", dropped[2])
        self.assertIn("the session already subscribes to acme/app#12 fix", again[1])
        self.assertEqual(["acme/app#12 fix", "!acme/app#13 fix"], self.lines())

    def test_add_from_output_subscribes_to_each_pull_request_a_push_or_create_shows(self) -> None:
        self.github.pull_request(12, ref="feature")
        self.github.pull_request(13, ref="elsewhere")
        output = ("To github.com:acme/app.git\n"
                  "   1a2b3c4..5d6e7f8  feature -> feature\n"
                  "   1a2b3c4..5d6e7f8  lonely -> lonely\n"
                  "https://github.com/acme/app/pull/13\n")

        code, out, err = self.project("add", "--from-output", "-", "--json", stdin=output)

        self.assertEqual(0, code, err)
        self.assertEqual(["acme/app#13 fix auto", "acme/app#12 fix auto"], json.loads(out)["added"])
        self.assertEqual(["acme/app#13 fix auto", "acme/app#12 fix auto"], self.lines())
        self.assertIn(["api", "repos/acme/app/pulls?head=acme:lonely&state=open"], self.github.calls)

    def test_add_from_output_keeps_an_existing_fix_line_and_skips_a_dropped_pull_request(self) -> None:
        self.origin("git@github.com:acme/app.git")
        watch.edit_subscriptions(self.root / "sessions" / "top-level", lambda lines: ["acme/app#12 fix",
                                                                                       "!acme/app#14 fix"])
        output = "https://github.com/acme/app/pull/12\nhttps://github.com/acme/app/pull/14\n"
        path = self.cwd / "output.txt"
        path.write_text(output)

        code, out, err = self.project("add", "--from-output", str(path))

        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith("the output shows no new pull request to fix\n"), out)
        self.assertEqual(["acme/app#12 fix", "!acme/app#14 fix"], self.lines())
        self.assertEqual([], self.github.calls, "a pull request the session holds or drops needs no lookup")

    def test_add_from_output_takes_a_link_only_in_the_origin_or_a_repository_the_output_pushed_to(self) -> None:
        self.origin("https://github.com/acme/app.git")
        for repo, number in (("acme/app", 12), ("acme/web", 13), ("other/lib", 14)):
            self.github.pull_request(number, repo=repo)
        output = ("To github.com:acme/web.git\n"
                  "https://github.com/acme/app/pull/12\n"
                  "https://github.com/acme/web/pull/13\n"
                  "See https://github.com/other/lib/pull/14 for the upstream fix\n")

        code, out, err = self.project("add", "--from-output", "-", "--json", stdin=output)

        self.assertEqual(0, code, err)
        self.assertEqual(["acme/app#12 fix auto", "acme/web#13 fix auto"], self.lines())
        self.assertNotIn(["api", "repos/other/lib/pulls/14"], self.github.calls)

    def test_add_from_output_takes_no_link_outside_a_checkout_without_a_push(self) -> None:
        self.github.pull_request(12)

        code, out, err = self.project("add", "--from-output", "-", stdin="https://github.com/acme/app/pull/12\n")

        self.assertEqual(0, code, err)
        self.assertEqual([], self.lines())
        self.assertEqual([], self.github.calls)

    def test_add_from_output_skips_a_link_that_is_no_open_pull_request(self) -> None:
        self.origin("git@github.com:acme/app.git")
        self.github.pull_request(41, state="MERGED")
        self.github.pull_request(42)
        output = ("Examples:\n"
                  "  $ gh stack checkout https://github.com/acme/app/pull/123\n"
                  "  $ gh stack link https://github.com/acme/app/pull/41 https://github.com/acme/app/pull/42\n")

        code, out, err = self.project("add", "--from-output", "-", stdin=output)

        self.assertEqual(0, code, err)
        self.assertEqual(["acme/app#42 fix auto"], self.lines())
        self.assertIn("project: could not look up acme/app#123: gh: Not Found (HTTP 404)", err)

    def test_add_from_output_adds_nothing_when_auto_start_is_off(self) -> None:
        (self.home / ".projector.toml").write_text("[watch]\nauto_start = false\n")

        code, out, err = self.project("add", "--from-output", "-", stdin="https://github.com/acme/app/pull/12\n")

        self.assertEqual(0, code, err)
        self.assertIn("watch.auto_start is false, so the output subscribes the session to nothing", out)
        self.assertEqual([], self.lines())

    def test_remove_drop_and_restore_change_one_kind_of_line(self) -> None:
        watch.edit_subscriptions(self.root / "sessions" / "top-level", lambda lines: [
            "acme/app#12 fix", f"acme/app#12 review {MODEL} octocat", f"acme/app review gpt-6 octocat"])

        dropped = self.project("drop", "acme/app#12", "--role", "review", "--json")
        restored = self.project("restore", "acme/app#12", "--role", "review", "--model", "gpt-6")
        removed = self.project("remove", "acme/app#12")
        nothing = self.project("remove", "acme/app#12")
        repository = self.project("remove", "acme/app", "--role", "review")

        self.assertEqual({"dropped": [f"!acme/app#12 review {MODEL} octocat", "!acme/app#12 review gpt-6 octocat"],
                          "schema_version": 2}, json.loads(dropped[1]))
        self.assertEqual("restored !acme/app#12 review gpt-6 octocat\n", restored[1])
        self.assertEqual("removed acme/app#12 fix\n", removed[1])
        self.assertIn("unchanged", nothing[1])
        self.assertEqual("removed acme/app review gpt-6 octocat\n", repository[1])
        self.assertEqual([f"acme/app#12 review {MODEL} octocat", f"!acme/app#12 review {MODEL} octocat"],
                         self.lines())

    def test_take_prints_and_empties_the_events_and_renews_the_session(self) -> None:
        folder = self.root / "sessions" / "top-level"
        watch.append_events(folder, ["CONFLICT acme/app#12 head=aaaaaaaa base=main: x — run fix-pr"])

        code, out, err = self.project("take", "--json")
        empty = self.project("take")

        self.assertEqual(0, code, err)
        self.assertEqual({"session": "top-level", "events": ["CONFLICT acme/app#12 head=aaaaaaaa base=main: x — "
                                                             "run fix-pr"], "schema_version": 2}, json.loads(out))
        self.assertEqual("", (folder / "events").read_text())
        self.assertEqual("", empty[1])
        self.assertEqual("mod", read_json(folder / "session.json")["deliverer"])

    def test_list_and_status_report_what_the_daemon_recorded(self) -> None:
        self.project("add", "acme/app#12")
        self.project("add", "acme/app", "--role", "review", "--model", MODEL, "--reviewer", "octocat")
        watch.write_json(self.root / "owners.json", {"acme/app#12 fix": "top-level",
                                                     f"acme/app#3 review {MODEL}": "top-level"})
        watch.write_json(self.root / "status.json", {"live_sessions": ["top-level"], "targets": ["acme/app#12"],
                                                     "repositories": ["acme/app"],
                                                     "last_pass": "2026-10-30T00:00:00+00:00",
                                                     "graphql": [{"at": watch.stamp(time.time()), "cost": 1},
                                                                 {"at": "2020-01-01T00:00:00+00:00", "cost": 9}]})

        listed = self.project("list")
        everyone = self.project("list", "--json", "--session", "top-level")
        status = self.project("status", "--json")

        self.assertEqual(f"acme/app#12 fix\tlive\towner\nacme/app review {MODEL} octocat\tlive\towns #3\n", listed[1])
        self.assertEqual([True, [3]], [json.loads(everyone[1])["subscriptions"][0]["owner"],
                                       json.loads(everyone[1])["subscriptions"][1]["owns"]])
        report = json.loads(status[1])
        self.assertEqual((["top-level"], ["acme/app#12"], 1), (report["live_sessions"], report["targets"],
                                                               report["graphql_points_last_hour"]))

    def test_a_command_refuses_a_missing_or_malformed_session(self) -> None:
        os.environ.pop("CLAUDE_CODE_SESSION_ID")

        missing = self.project("take")
        malformed = self.project("take", "--session", "../escape")

        self.assertEqual(2, missing[0])
        self.assertIn("pass --session", missing[2])
        self.assertEqual(2, malformed[0])

    def test_stop_with_no_daemon_running_says_so_and_starts_none(self) -> None:
        self.assertEqual((0, "no watch daemon is running\n", ""), self.project("stop"))
        self.assertEqual([], self.spawned)

    def test_stream_renews_the_session_and_prints_each_event_as_it_arrives(self) -> None:
        folder = self.root / "sessions" / "s1"
        emitted: list[str] = []
        ensured: list[int] = []
        clock = Clock()

        class Done(Exception):
            pass

        def sleep(seconds: float) -> None:
            clock.advance(seconds)
            if clock.mono() == 1002:
                watch.append_events(folder, ["FINDING acme/app#1 x — run fix-pr"])
            if clock.mono() >= 1040:
                raise Done

        with self.assertRaises(Done):
            watch.stream(self.root, "s1", emitted.append, lambda: ensured.append(1), wall=clock.wall,
                         mono=clock.mono, sleep=sleep)

        self.assertEqual(["FINDING acme/app#1 x — run fix-pr"], emitted)
        self.assertEqual(2, len(ensured))
        self.assertEqual("stream", read_json(folder / "session.json")["deliverer"])


class SettingTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.home = Path(scratch.name)
        environ = mock.patch.dict(os.environ, {"HOME": str(self.home), "PROJECTOR_OFFLINE": "1"})
        environ.start()
        self.addCleanup(environ.stop)

    def config(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.chdir(self.home), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["config", *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_watch_settings_take_whole_numbers_and_the_interval_at_least_30(self) -> None:
        too_often = self.config("set", "watch.interval", "20", "--user")
        not_a_number = self.config("set", "watch.renotify", "soon", "--user")
        interval = self.config("set", "watch.interval", "45", "--user")
        stall = self.config("set", "watch.review_stall", "600", "--user")

        self.assertEqual(2, too_often[0])
        self.assertIn("watch.interval must be a whole number of at least 30", too_often[2])
        self.assertEqual(2, not_a_number[0])
        self.assertEqual((0, 0), (interval[0], stall[0]))
        self.assertEqual("45\n", self.config("get", "watch.interval")[1])
        self.assertEqual(watch.Settings(interval=45, review_stall=600), watch.user_settings())

    def test_the_daemon_reads_a_hand_set_interval_below_the_minimum_as_the_minimum(self) -> None:
        (self.home / ".projector.toml").write_text("[watch]\ninterval = 5\nfull_every = 1200\n")

        self.assertEqual(watch.Settings(interval=30, full_every=1200), watch.user_settings())

    def test_a_setting_of_the_wrong_type_is_refused(self) -> None:
        (self.home / ".projector.toml").write_text("[watch]\nrenotify = true\n")

        with self.assertRaisesRegex(Exception, "watch.renotify must be a whole number"):
            watch.user_settings()


class DefaultBranchFetchTests(WatchCase):
    """Each full pass fetches a live checkout's default branch into Projector's own ref, and no ref you use."""

    def setUp(self) -> None:
        super().setUp()
        from tests.scratch import copy_of
        from tests.test_config import cloned_repository
        base, _ = copy_of(cloned_repository)
        self.base = base.resolve()
        self.home = self.base / "home"
        self.repo = self.home / "work" / "repo"
        environment = mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": str(self.scratch / "state")})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.daemon.relay.close)
        self.renew("s1")
        write_json(self.folder("s1") / "agent.json", {"schema_version": 1, "cwd": str(self.repo)})

    def git(self, cwd: Path, *arguments: str) -> str:
        from tests.test_config import git
        return git(cwd, *arguments)

    def merge_on_origin(self, text: str, *, amend: bool = False) -> str:
        """Commit `text` as the policy on `origin`'s default branch, or replace its last commit, without fetching."""

        seed = self.base / "seed"
        (seed / ".projector.toml").write_text(text)
        self.git(seed, "commit", "-q", "-a", *(["--amend"] if amend else []), "-m", "Change policy")
        self.git(self.base / "origin.git", "fetch", "-q", str(seed), "+main:main")
        return self.git(seed, "rev-parse", "HEAD")

    def full_pass(self) -> None:
        self.daemon.tick()
        for _, future in list(self.daemon.fetching.values()):
            future.result(60)
        self.daemon.fetch_defaults(False)
        self.daemon.write_status(self.clock.wall())

    def ref(self, name: str) -> str:
        """The commit `name` names in the clone, or an empty string when it names none."""
        return subprocess.run(["git", "rev-parse", "--verify", "-q", name], cwd=self.repo, capture_output=True,
                              text=True).stdout.strip()

    def gate_checks(self) -> object:
        from projector.config import load
        return load(self.repo).get("review.gate_checks")

    def test_a_merged_policy_applies_after_a_full_pass_and_your_refs_stay_put(self) -> None:
        before = self.ref("refs/remotes/origin/main")
        merged = self.merge_on_origin('[review]\ngate_checks = "check"\n')

        self.full_pass()

        self.assertEqual(("check", merged, before), (self.gate_checks(), self.ref("refs/projector/default/origin/main"),
                                                     self.ref("refs/remotes/origin/main")))
        self.assertNotIn("behind", self.git(self.repo, "status", "-sb"))
        self.assertEqual("ok", watch.status(self.root, self.clock.wall())["default_branches"][str(self.repo)]["state"])
        self.git(self.repo, "fetch", "-q", "origin")
        self.assertEqual(merged, self.ref("refs/remotes/origin/main"))

    def test_after_a_force_push_your_own_fetch_wins(self) -> None:
        self.merge_on_origin('[review]\ngate_checks = "check"\n')
        self.full_pass()

        forced = self.merge_on_origin('[review]\ngate_checks = "forced"\n', amend=True)
        self.git(self.repo, "fetch", "-q", "origin")

        self.assertEqual((forced, "forced"), (self.ref("refs/remotes/origin/main"), self.gate_checks()))

    def test_a_failed_fetch_keeps_the_last_commit_and_reports_the_error(self) -> None:
        merged = self.merge_on_origin('[review]\ngate_checks = "check"\n')
        self.full_pass()
        self.git(self.repo, "remote", "set-url", "origin", str(self.base / "missing.git"))
        self.merge_on_origin('[review]\ngate_checks = "later"\n')

        self.advance(self.settings.full_every + 1)
        self.full_pass()

        found = watch.status(self.root, self.clock.wall())["default_branches"][str(self.repo)]
        self.assertEqual(("error", merged, "check"),
                         (found["state"], self.ref("refs/projector/default/origin/main"), self.gate_checks()))
        self.assertIn("missing.git", found["error"])

    def test_an_inherited_git_dir_does_not_send_the_fetch_to_another_repository(self) -> None:
        other = self.base / "other"
        self.git(self.base, "init", "-q", "-b", "main", str(other))
        self.git(other, "remote", "add", "origin", str(self.base / "origin.git"))
        merged = self.merge_on_origin('[review]\ngate_checks = "check"\n')

        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
            self.full_pass()

        found = watch.status(self.root, self.clock.wall())["default_branches"][str(self.repo)]
        stray = subprocess.run(["git", "for-each-ref", "refs/projector/"], cwd=other, capture_output=True,
                               text=True).stdout
        self.assertEqual(("ok", merged, ""), (found["state"], self.ref("refs/projector/default/origin/main"), stray))

    def test_fetch_default_false_fetches_nothing(self) -> None:
        (self.home / ".projector.toml").write_text("[watch]\nfetch_default = false\n")
        self.merge_on_origin('[review]\ngate_checks = "check"\n')

        self.full_pass()

        self.assertEqual(("", {}), (self.ref("refs/projector/default/origin/main"), self.daemon.fetched))


if __name__ == "__main__":
    unittest.main()
