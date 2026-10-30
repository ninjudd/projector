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

from projector import cli, review, watch
from projector.inbox import read_json, try_lock, unlock

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
MODEL = "claude-opus-5-5"
REASONS = {200: "OK", 304: "Not Modified", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


def node(number, *, repo="acme/app", state="OPEN", draft=False, decision=None, head=SHA_A, ref="feature",
         base="main", mergeable="MERGEABLE", merged_at=None, merge_commit=None, author="operator", verdicts=(),
         changes=None, threads=(), comments=()):
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
    }


def thread(thread_id, *, path="src/x.py", line=3, who="bugbot", body="P1 nil deref", resolved=False, replies=()):
    comments = [{"databaseId": 1, "author": {"login": who}, "body": body}, *replies]
    return {"id": thread_id, "isResolved": resolved, "path": path, "line": line,
            "first": {"nodes": comments[:1]}, "last": {"nodes": comments[-1:]}}


def rest_review(review_id, *, who="codex", body="Body-only concern", sha=SHA_A, state="COMMENTED"):
    return {"id": review_id, "state": state, "user": {"login": who}, "body": body, "commit_id": sha}


def projector_verdict(sha, verdict="changes-requested", *, at="2026-10-12T10:00:00Z", association="COLLABORATOR",
                      model=MODEL, who="operator"):
    """A GraphQL review node by `who` whose body carries the marker line `project review publish` writes."""
    named = f"model={model} " if model else ""
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector=0.7.12 {named}effort=high "
              f"sha={sha} findings=1 seconds=60 covered=1/1 -->")
    return {"author": {"login": who}, "authorAssociation": association, "submittedAt": at,
            "body": f"Projector review\n\n{marker}\n\nSummary"}


FINDING = "<!-- projector-finding v=1 priority=P2 --> A nil deref"


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

    def pull_request(self, number, *, repo="acme/app", reviews=(), requested=(), teams=(), **fields) -> None:
        self.repository(repo)["pulls"][number] = {"node": node(number, repo=repo, **fields), "requested": list(requested),
                                                  "teams": list(teams), "reviews": list(reviews)}

    def queried(self) -> list[str]:
        found = []
        for call in self.calls:
            if call[:2] == ["api", "graphql"]:
                fields = dict(arg.split("=", 1) for arg in call if re.match(r"^[orn]=", arg))
                found.append(f"{fields['o']}/{fields['r']}#{fields['n']}")
        return found

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
            if include or paginate or etag or set(fields) != {"query", "o", "r", "n"}:
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
        repo = self.repos.get(f"{fields['o']}/{fields['r']}".lower())
        pull = (repo or {}).get("pulls", {}).get(int(fields["n"]))
        if pull is None:
            kind = "PullRequest" if repo else "Repository"
            body = {"data": {"repository": {"pullRequest": None} if repo else None},
                    "errors": [{"type": "NOT_FOUND", "message": f"Could not resolve to a {kind}."}]}
            return watch.GhResult(1, json.dumps(body), f"gh: Could not resolve to a {kind}.")
        data = {"rateLimit": {"cost": 1}, "repository": {"visibility": repo["visibility"], "nameWithOwner": repo["name"],
                                                         "pullRequest": pull["node"]}}
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


def new_pr(number: int, ref: str, session: str, head: str = SHA_A) -> str:
    return (f"NEW PR acme/app#{number} ({ref}) head={head[:7]}: unreviewed, needs an exact-head review — "
            f"{review_action(number, session)}")


def new_head(number: int, ref: str, old: str, new: str, session: str) -> str:
    return (f"NEW HEAD acme/app#{number} ({ref}): {old[:7]} -> {new[:7]}, needs an exact-head review — "
            f"{review_action(number, session)}")


NO_REVIEWER = ", and no session on this machine reviews it"


class FixRoleTests(WatchCase):
    """What the `fix` owner hears."""

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
            1, draft=True, decision="CHANGES_REQUESTED",
            changes={"author": {"login": "teammate"}, "body": "Please split this", "commit": {"oid": SHA_A}},
            threads=[thread("T1", body=marker), thread("T0", resolved=True)],
            reviews=[rest_review(77, who="codex", body="Body-only concern"),
                     rest_review(78, who="operator", body="<!-- projector-reply v=1 -->"),
                     rest_review(79, who="teammate", body="Approving", state="APPROVED")])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"DRAFT acme/app#1 head=aaaaaaaa: no review has signed off this head{NO_REVIEWER}; the work, if any, is "
            "in its threads and review bodies — tell the user that no session reviews acme/app#1",
            f"VERDICT acme/app#1 CHANGES_REQUESTED on aaaaaaaa [teammate]: Please split this — {fix_action(1)}",
            f"FINDING acme/app#1 src/x.py:3 [bugbot]: P1 nil deref — {fix_action(1)}",
            f"REVIEW acme/app#1 by [codex] on aaaaaaaa: Body-only concern — {fix_action(1)}",
        ], events["fixer"])
        self.assertEqual({f"draft:1:{SHA_A}", f"verdict:teammate:{SHA_A}", "T1", "review:77"},
                         set(self.state("fixer")["fix"]["acme/app#1 fix"]))

    def test_a_draft_that_a_session_reviews_waits_for_its_review(self) -> None:
        self.github.pull_request(1, draft=True)
        self.fixer("acme/app#1 fix")
        self.subscribe("reviewer", f"acme/app#1 review {MODEL} operator")
        self.renew("reviewer")

        events = self.cycle()

        self.assertEqual(["DRAFT acme/app#1 head=aaaaaaaa: no review has signed off this head; the work, if any, is in "
                          "its threads and review bodies — wait for its review"], events["fixer"])

    def test_a_session_that_fixes_and_reviews_a_pull_request_hears_no_draft(self) -> None:
        self.github.pull_request(1, draft=True)
        self.fixer("acme/app#1 fix", f"acme/app#1 review {MODEL} operator")

        events = self.cycle()

        self.assertEqual([new_pr(1, "feature", "fixer")], events["fixer"])

    def test_a_projector_review_says_how_many_suggestions_its_body_counts(self) -> None:
        def projector_review(review_id, count):
            marker = (f"<!-- projector-review v=1 verdict=clean projector=0.7.20 model={MODEL} sha={SHA_A} "
                      "findings=0 seconds=60 covered=1/1 -->")
            counted = "" if count is None else f"<!-- projector-suggestions v=1 count={count} -->\n"
            return rest_review(review_id, who="operator", body=f"Projector review\n\n{marker}\n\n{counted}Clean.")

        self.github.pull_request(1, reviews=[projector_review(81, 2), projector_review(82, 1), projector_review(83, 0),
                                             projector_review(84, None)])
        self.fixer("acme/app#1 fix")

        events = self.cycle()

        self.assertEqual([
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 2 suggestions: Projector review Clean. — {fix_action(1)}",
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa, listing 1 suggestion: Projector review Clean. — {fix_action(1)}",
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa: Projector review Clean. — {fix_action(1)}",
            f"REVIEW acme/app#1 by [operator] on aaaaaaaa: Projector review Clean. — {fix_action(1)}",
        ], events["fixer"])

    def test_a_later_pass_repeats_only_after_the_renotify_interval(self) -> None:
        self.github.pull_request(1, draft=True, threads=[thread("T1")], reviews=[rest_review(5)])
        self.fixer("acme/app#1 fix")

        first = self.cycle()["fixer"]
        self.advance(60)
        second = self.cycle()["fixer"]
        self.advance(900)
        third = self.cycle()["fixer"]

        self.assertEqual(3, len(first), first)
        self.assertEqual([], second)
        self.assertEqual([
            f"DRAFT acme/app#1 (still open) head=aaaaaaaa: still not signed off{NO_REVIEWER} — tell the user that no "
            "session reviews acme/app#1",
            f"FINDING acme/app#1 (still open) src/x.py:3 [bugbot]: P1 nil deref — {fix_action(1)}",
        ], third)

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

    def test_a_merged_pull_request_is_announced_once_and_its_lines_end(self) -> None:
        self.github.pull_request(1, draft=True, threads=[thread("T1")])
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

    def test_reports_a_moved_head_then_a_response_once_per_head(self) -> None:
        self.github.pull_request(1, head=SHA_B, draft=True)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True)
        moved = self.cycle()["reviewer"]
        suppressed = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[thread("T1")])
        under_review = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[thread("T1", resolved=True)])
        responded = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)], threads=[thread("T2")])
        reopened = self.cycle()["reviewer"]
        flag = self.state("reviewer")["review"][f"acme/app#1 review {MODEL}"]["flag"]
        self.github.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)],
                                 threads=[thread("T2", resolved=True)])
        cross_author = self.cycle()["reviewer"]

        self.assertEqual([new_head(1, "feature", SHA_B, SHA_A, "reviewer")], moved)
        self.assertEqual(([], [], [], []), (suppressed, under_review, quiet, reopened))
        self.assertEqual("-", flag)
        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"], responded)
        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: changes requested with every thread "
                          f"resolved; re-review this head, and a clean one is a COMMENT, never an approval — "
                          f"{rereview(1, 'reviewer')}"], cross_author)

    def test_reports_an_author_reply_on_an_open_finding_once_per_reply(self) -> None:
        finding = {"databaseId": 11, "body": FINDING}

        def finding_on(*replies):
            found = thread("T1", body=FINDING, replies=replies)
            found["first"]["nodes"] = [finding]
            return found

        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_on()])
        self.reviewer(1)
        self.cycle()

        decline = {"databaseId": 12, "body": "<!-- projector-reply v=1 --> Declined: the caller checks for nil."}
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_on(decline)])
        replied = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]
        kept = {"databaseId": 13, "body": "<!-- projector-verify v=1 result=kept --> The second caller does not."}
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_on(decline, kept)])
        after_verify = self.cycle()["reviewer"]
        again = {"databaseId": 14, "body": "Fair, but that caller is dead code."}
        self.github.pull_request(1, head=SHA_A, draft=True, threads=[finding_on(decline, kept, again)])
        replied_again = self.cycle()["reviewer"]

        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft and the author replied on an "
                          f"open finding; re-review this head and sign off if clean — {rereview(1, 'reviewer')}"],
                         replied)
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

    def test_a_hold_is_never_a_response_and_lifting_it_is_one(self) -> None:
        hold = {"author": {"login": "operator"},
                "body": f"Held in draft while the following changes are implemented:\n\n- Rename `x`\n\n"
                        f"{review.HOLD_MARKER}\n"}
        self.github.pull_request(1, head=SHA_A)
        self.reviewer(1)
        self.cycle()
        self.cycle()

        self.github.pull_request(1, head=SHA_A, comments=[hold])
        posted = self.cycle()["reviewer"]
        flag = self.state("reviewer")["review"][f"acme/app#1 review {MODEL}"]["flag"]
        self.github.pull_request(1, head=SHA_A, draft=True, comments=[hold])
        held = self.cycle()["reviewer"]
        still_held = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True)
        lifted = self.cycle()["reviewer"]
        quiet = self.cycle()["reviewer"]

        self.assertEqual(([], [], []), (posted, held, still_held))
        self.assertEqual("held", flag)
        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft now that its hold is lifted; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"], lifted)
        self.assertEqual([], quiet)

    def test_drafting_before_the_hold_lands_asks_to_re_review_the_unchanged_head(self) -> None:
        self.github.pull_request(1, head=SHA_A)
        self.reviewer(1)
        self.cycle()
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True)
        drafted_first = self.cycle()["reviewer"]

        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"], drafted_first)

    def test_a_hold_seen_on_a_new_head_asks_for_it_again_once_lifted(self) -> None:
        hold = {"author": {"login": "operator"}, "body": f"Held\n\n- Rename `x`\n\n{review.HOLD_MARKER}"}
        self.github.pull_request(1, head=SHA_B, draft=True)
        self.reviewer(1)
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True, comments=[hold])
        moved = self.cycle()["reviewer"]
        self.github.pull_request(1, head=SHA_A, draft=True)
        lifted = self.cycle()["reviewer"]

        self.assertEqual([new_head(1, "feature", SHA_B, SHA_A, "reviewer")], moved)
        self.assertEqual(1, len(lifted), lifted)
        self.assertIn("still a draft now that its hold is lifted", lifted[0])

    def test_a_hold_marker_from_anyone_but_the_author_holds_nothing(self) -> None:
        planted = {"author": {"login": "passerby"}, "body": review.HOLD_MARKER}
        self.github.pull_request(1, head=SHA_A)
        self.reviewer(1)
        self.cycle()
        self.cycle()

        self.github.pull_request(1, head=SHA_A, draft=True, comments=[planted])

        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"],
                         self.cycle()["reviewer"])

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

        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"], known)
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

    def test_only_the_authors_own_draft_waits_on_a_clean_review(self) -> None:
        # A review never moves another author's draft state, so that draft stays a draft after a clean review
        # dismisses the change request it settled.
        def both(**fields):
            self.github.pull_request(1, ref="mine", draft=True, **fields)
            self.github.pull_request(2, ref="asked", author="teammate", draft=True, **fields)

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
        self.assertEqual([f"RESPONDED acme/app#1 (mine) head=bbbbbbb: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'reviewer')}"], settled)

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
            self.github.pull_request(4, ref="passerby", author="teammate", threads=outsider_threads,
                                     verdicts=[projector_verdict(SHA_A, association="NONE")])
            # In a private one, every review counts.
            self.github.pull_request(5, repo="acme/secret", ref="private", author="teammate",
                                     threads=outsider_threads,
                                     verdicts=[projector_verdict(SHA_A, association="NONE")])

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
        self.assertEqual([f"RESPONDED acme/app#1 (feature) head=aaaaaaa: still a draft with every thread resolved; "
                          f"re-review this head and sign off if clean — {rereview(1, 'B')}"], answered["B"])
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

        again = watch.ensure_daemon(self.root, "1.0", cli.newer_release)
        os.kill(first, signal.SIGKILL)
        self.wait_for(lambda: not watch.held(self.root))
        restarted = watch.ensure_daemon(self.root, "1.0", cli.newer_release)
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
                  "https://github.com/acme/web/pull/13\n")

        self.assertEqual(([("acme/web", 13)], [("acme/app", "feature"), ("acme/app", "other")]),
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
        self.assertEqual({"added": ["acme/app#12 fix"], "delivery": "mod", "schema_version": 2}, json.loads(out))

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
        watch.edit_subscriptions(self.root / "sessions" / "top-level", lambda lines: ["acme/app#12 fix",
                                                                                       "!acme/app#14 fix"])
        output = "https://github.com/acme/app/pull/12\nhttps://github.com/acme/app/pull/14\n"
        path = self.cwd / "output.txt"
        path.write_text(output)

        code, out, err = self.project("add", "--from-output", str(path))

        self.assertEqual(0, code, err)
        self.assertTrue(out.startswith("the output shows no new pull request to fix\n"), out)
        self.assertEqual(["acme/app#12 fix", "!acme/app#14 fix"], self.lines())

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


if __name__ == "__main__":
    unittest.main()
