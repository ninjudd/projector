"""Drive both watcher scripts through a fake ``gh`` and check what they say.

The fake serves one fixture per pull request and applies every ``--jq``
filter with the system ``jq``, so the scripts' own filters are what run. Each
test makes single passes with ``--once``, except the one that checks a running
watch exits; the state file carries what one pass announced into the next,
exactly as it does between cycles of a live watch.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
THREADS = ROOT / "skills" / "start-watch-loop" / "scripts" / "watch-threads.sh"
PRS = ROOT / "skills" / "start-review-loop" / "scripts" / "watch-prs.sh"

SHA_A = "a" * 40
SHA_B = "b" * 40
DONE = "DONE every tracked pull request has merged or closed; the watch is exiting"

FAKE_GH = r"""#!/usr/bin/env bash
# Serves fixtures from $FAKE_GH_DIR:
#   <owner>/<repo>/<n>.json          the GraphQL pullRequest node; absent means NOT_FOUND
#   <owner>/<repo>/<n>.reviews.json  the REST reviews array; absent means []
#   <owner>/<repo>/visibility        the repository's GraphQL visibility; absent means null
# `pr list --repo <owner>/<repo> --author <login>` lists the open fixtures whose
# node names that author, and `--search user-review-requested:<login>` the open
# fixtures whose node requests that login's review.
# Logs one line per call to $FAKE_GH_DIR/calls.log. With FAKE_GH_DOWN_AFTER set,
# every call past that many already-logged calls fails like a network error.
# With FAKE_GH_LATE_LINE set, the first call appends it to $FAKE_GH_TRACKED, so
# the line reaches the watch's first pass but not the check that runs before it.
set -u
log="$FAKE_GH_DIR/calls.log"
count=0
[ -f "$log" ] && count=$(wc -l < "$log" | tr -d ' ')
printf '%s\n' "$*" >> "$log"
if [ -n "${FAKE_GH_LATE_LINE:-}" ] && [ "$count" -eq 0 ]; then
  printf '%s\n' "$FAKE_GH_LATE_LINE" >> "$FAKE_GH_TRACKED"
fi
if [ -n "${FAKE_GH_DOWN_AFTER:-}" ] && [ "$count" -ge "$FAKE_GH_DOWN_AFTER" ]; then
  echo 'Post "https://api.github.com/graphql": dial tcp 127.0.0.1:9: connect: connection refused' >&2
  exit 1
fi
mode=""; owner=""; repo=""; num=""; jq_expr="."; path=""; slug=""; author=""; requested=""
while [ $# -gt 0 ]; do
  case "$1" in
    api) shift ;;
    pr) mode=list; shift 2 ;;
    --repo) slug="$2"; shift 2 ;;
    --author) author="$2"; shift 2 ;;
    --search)
      case "$2" in user-review-requested:*) requested="${2#user-review-requested:}" ;;
        *) echo "fake gh: unsupported search: $2" >&2; exit 3 ;; esac
      shift 2 ;;
    --state|--limit|--json) shift 2 ;;
    graphql) mode=graphql; shift ;;
    -f) case "$2" in o=*) owner="${2#o=}" ;; r=*) repo="${2#r=}" ;; esac; shift 2 ;;
    -F) case "$2" in n=*) num="${2#n=}" ;; esac; shift 2 ;;
    --jq) jq_expr="$2"; shift 2 ;;
    --paginate) shift ;;
    repos/*) mode=rest; path="$1"; shift ;;
    *) echo "fake gh: unsupported argument: $1" >&2; exit 3 ;;
  esac
done
case "$mode" in
  list)
    [ -d "$FAKE_GH_DIR/$slug" ] || { echo "GraphQL: Could not resolve to a Repository" >&2; exit 1; }
    for f in "$FAKE_GH_DIR/$slug"/*.json; do
      case "$f" in *.reviews.json|*'*'*) continue ;; esac
      jq -c --arg a "$author" --arg r "$requested" --argjson n "$(basename "$f" .json)" \
        'select(.state == "OPEN")
         | select(if $r != "" then any(.reviewRequests.nodes[]; .requestedReviewer.login == $r)
                  else .author.login == $a end)
         | {number: $n}' < "$f"
    done | jq -s 'sort_by(.number)' | jq -r "$jq_expr"
    ;;
  graphql)
    if [ ! -d "$FAKE_GH_DIR/$owner/$repo" ]; then
      printf '{"data":{"repository":null},"errors":[{"type":"NOT_FOUND","message":"Could not resolve to a Repository with the name '"'"'%s/%s'"'"'."}]}' "$owner" "$repo"
      echo "gh: Could not resolve to a Repository with the name '$owner/$repo'." >&2
      exit 1
    fi
    f="$FAKE_GH_DIR/$owner/$repo/$num.json"
    if [ ! -f "$f" ]; then
      printf '{"data":{"repository":{"pullRequest":null}},"errors":[{"type":"NOT_FOUND","message":"Could not resolve to a PullRequest with the number of %s."}]}' "$num"
      echo "gh: Could not resolve to a PullRequest with the number of $num." >&2
      exit 1
    fi
    visibility=null
    [ -f "$FAKE_GH_DIR/$owner/$repo/visibility" ] && visibility="\"$(cat "$FAKE_GH_DIR/$owner/$repo/visibility")\""
    printf '{"data":{"repository":{"visibility":%s,"pullRequest":%s}}}' "$visibility" "$(cat "$f")" | jq -r "$jq_expr"
    ;;
  rest)
    rest="${path#repos/}"; owner="${rest%%/*}"; rest="${rest#*/}"
    repo="${rest%%/*}"; rest="${rest#*/pulls/}"; num="${rest%%/*}"
    [ -f "$FAKE_GH_DIR/$owner/$repo/$num.json" ] || { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    f="$FAKE_GH_DIR/$owner/$repo/$num.reviews.json"
    if [ -f "$f" ]; then jq -r "$jq_expr" < "$f"; else printf '[]' | jq -r "$jq_expr"; fi
    ;;
  *) echo "fake gh: unsupported call: $*" >&2; exit 3 ;;
esac
"""


def thread(thread_id, *, path="src/x.py", line=3, who="bugbot", body="P1 nil deref", resolved=False):
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": path,
        "line": line,
        "comments": {"nodes": [{"author": {"login": who}, "body": body}]},
    }


def review(review_id, *, who="codex", body="Body-only concern", sha=SHA_A, state="COMMENTED"):
    return {"id": review_id, "state": state, "user": {"login": who}, "body": body, "commit_id": sha}


def projector_verdict(sha, verdict="changes-requested", *, at="2026-10-12T10:00:00Z", association="COLLABORATOR",
                      model="claude-opus-5-5", who="operator"):
    """A GraphQL review node by `who` whose body carries the marker line `project review publish` writes,
    naming `model`, or no model when it is None."""
    named = f"model={model} " if model else ""
    marker = (f"<!-- projector-review v=1 verdict={verdict} projector=0.7.12 {named}effort=high "
              f"sha={sha} findings=1 seconds=60 covered=1/1 -->")
    return {"author": {"login": who}, "authorAssociation": association, "submittedAt": at,
            "body": f"Projector review\n\n{marker}\n\nSummary"}


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "the watcher tests need bash and jq")
class WatcherCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="projector-watchers-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        bin_dir = self.dir / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "gh"
        fake.write_text(FAKE_GH)
        fake.chmod(0o755)
        self.fixtures = self.dir / "fixtures"
        self.fixtures.mkdir()
        self.env = dict(os.environ)
        self.env.pop("FAKE_GH_DOWN_AFTER", None)
        self.env.pop("FAKE_GH_LATE_LINE", None)
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        self.env["FAKE_GH_DIR"] = str(self.fixtures)
        self.tracked = self.dir / "tracked"
        self.tracked.write_text("")
        self.state = self.dir / "state"

    def pull_request(
        self,
        number,
        *,
        repo="acme/app",
        state="OPEN",
        draft=False,
        decision=None,
        head=SHA_A,
        ref="feature",
        base="main",
        mergeable="MERGEABLE",
        author="operator",
        requested=(),
        verdict=None,
        verdicts=(),
        threads=(),
        reviews=(),
        comments=(),
    ) -> None:
        directory = self.fixtures / repo
        directory.mkdir(parents=True, exist_ok=True)
        node = {
            "state": state,
            "isDraft": draft,
            "reviewDecision": decision,
            "headRefOid": head,
            "headRefName": ref,
            "baseRefName": base,
            "mergeable": mergeable,
            "author": {"login": author},
            "reviewRequests": {"nodes": [{"requestedReviewer": {"login": login}} for login in requested]},
            "reviews": {"nodes": ([verdict] if verdict else []) + list(verdicts)},
            "reviewThreads": {"nodes": list(threads)},
            "comments": {"nodes": list(comments)},
        }
        (directory / f"{number}.json").write_text(json.dumps(node))
        (directory / f"{number}.reviews.json").write_text(json.dumps(list(reviews)))

    def track(self, *entries) -> None:
        self.tracked.write_text("".join(f"{entry}\n" for entry in entries))

    def run_watcher(self, script, *args, down_after=None, late_line=None, timeout=120):
        """Make one pass. The call log, the failure switch and the late line cover this pass only."""
        log = self.fixtures / "calls.log"
        if log.exists():
            log.unlink()
        env = dict(self.env)
        if down_after is not None:
            env["FAKE_GH_DOWN_AFTER"] = str(down_after)
        if late_line is not None:
            env["FAKE_GH_LATE_LINE"] = late_line
            env["FAKE_GH_TRACKED"] = str(self.tracked)
        return subprocess.run(
            ["bash", str(script), "--tracked", str(self.tracked), "--state", str(self.state), "--once", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=timeout,
        )

    def calls(self) -> str:
        log = self.fixtures / "calls.log"
        return log.read_text() if log.exists() else ""

    def state_rows(self):
        return [line.split() for line in self.state.read_text().splitlines() if line.strip()]

    def assert_ok(self, result) -> None:
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)


class WatchThreadsTests(WatcherCase):
    def test_events_come_only_from_tracked_pull_requests(self) -> None:
        self.pull_request(1, threads=[thread("T1", body="P2 off by one")])
        self.pull_request(
            2,
            draft=True,
            decision="CHANGES_REQUESTED",
            verdict={"author": {"login": "teammate"}, "body": "Split this", "commit": {"oid": SHA_A}},
            threads=[thread("T2")],
            reviews=[review(9)],
        )
        self.tracked.write_text("# the loop's own pull requests\n\n   acme/app#1  \n")

        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual(["FINDING acme/app#1 src/x.py:3 [bugbot] — P2 off by one"], result.stdout.splitlines())
        self.assertNotIn(" n=2 ", self.calls())
        self.assertNotIn("pulls/2/", self.calls())

    def test_reports_every_kind_of_outstanding_work_on_a_tracked_pull_request(self) -> None:
        marker = f"<!-- projector-finding v=1 priority=P1 sha={SHA_A} -->\nP1 nil deref"
        self.pull_request(
            1,
            draft=True,
            decision="CHANGES_REQUESTED",
            verdict={"author": {"login": "teammate"}, "body": "Please split this", "commit": {"oid": SHA_A}},
            threads=[thread("T1", body=marker), thread("T0", resolved=True)],
            reviews=[
                review(77, who="codex", body="Body-only concern"),
                review(78, who="operator", body="<!-- projector-reply v=1 -->"),
                review(79, who="teammate", body="Approving", state="APPROVED"),
            ],
        )
        self.track("acme/app#1")

        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual(
            [
                "DRAFT acme/app#1 head=aaaaaaaa — no review loop has signed off this head; "
                "the work, if any, is in its threads and review bodies",
                "VERDICT acme/app#1 CHANGES_REQUESTED on aaaaaaaa [teammate] — Please split this",
                "FINDING acme/app#1 src/x.py:3 [bugbot] — P1 nil deref",
                "REVIEW acme/app#1 by [codex] on aaaaaaaa — Body-only concern",
            ],
            result.stdout.splitlines(),
        )
        self.assertEqual(
            {
                (f"draft:1:{SHA_A}", "acme/app", "1"),
                (f"verdict:teammate:{SHA_A}", "acme/app", "1"),
                ("T1", "acme/app", "1"),
                ("review:77", "acme/app", "1"),
            },
            {tuple(row[:3]) for row in self.state_rows()},
        )

    def test_a_later_pass_repeats_only_after_the_renotify_interval(self) -> None:
        self.pull_request(1, draft=True, threads=[thread("T1")], reviews=[review(5)])
        self.track("acme/app#1")

        first = self.run_watcher(THREADS)
        second = self.run_watcher(THREADS)
        third = self.run_watcher(THREADS, "--renotify", "0")

        self.assert_ok(first)
        self.assertEqual(3, len(first.stdout.splitlines()), first.stdout)
        self.assert_ok(second)
        self.assertEqual("", second.stdout)
        self.assert_ok(third)
        self.assertEqual(
            [
                "DRAFT (still open) acme/app#1 head=aaaaaaaa — still not signed off",
                "FINDING (still open) acme/app#1 src/x.py:3 [bugbot] — P1 nil deref",
            ],
            third.stdout.splitlines(),
        )

    def test_reports_a_conflict_with_the_base_until_a_push_clears_it(self) -> None:
        self.pull_request(1, base="parent", mergeable="CONFLICTING")
        self.track("acme/app#1")

        first = self.run_watcher(THREADS)
        second = self.run_watcher(THREADS)
        third = self.run_watcher(THREADS, "--renotify", "0")
        self.pull_request(1, head=SHA_B, base="parent", mergeable="MERGEABLE")
        merged = self.run_watcher(THREADS)

        self.assert_ok(first)
        self.assertEqual(
            [
                "CONFLICT acme/app#1 head=aaaaaaaa base=parent — "
                "the branch conflicts with its base; merge the base and resolve it"
            ],
            first.stdout.splitlines(),
        )
        self.assert_ok(second)
        self.assertEqual("", second.stdout)
        self.assert_ok(third)
        self.assertEqual(
            ["CONFLICT (still open) acme/app#1 head=aaaaaaaa base=parent — still conflicts with its base"],
            third.stdout.splitlines(),
        )
        self.assert_ok(merged)
        self.assertEqual("", merged.stdout)
        self.assertEqual([], self.state_rows())

    def test_a_base_that_moves_into_conflict_announces_under_the_same_head(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(THREADS))

        self.pull_request(1, mergeable="CONFLICTING")
        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual(1, len(result.stdout.splitlines()), result.stdout)
        self.assertTrue(result.stdout.startswith("CONFLICT acme/app#1 head=aaaaaaaa base=main"), result.stdout)

    def test_a_conflict_is_not_new_again_after_github_recomputes_it(self) -> None:
        # GitHub answers UNKNOWN between a base move and its recomputation.
        self.pull_request(1, mergeable="CONFLICTING")
        self.track("acme/app#1")

        first = self.run_watcher(THREADS)
        self.pull_request(1, mergeable="UNKNOWN")
        recomputing = self.run_watcher(THREADS)
        self.pull_request(1, mergeable="CONFLICTING")
        recomputed = self.run_watcher(THREADS)

        self.assert_ok(first)
        self.assertEqual(1, len(first.stdout.splitlines()), first.stdout)
        self.assert_ok(recomputing)
        self.assertEqual("", recomputing.stdout)
        self.assert_ok(recomputed)
        self.assertEqual("", recomputed.stdout)
        self.assertEqual([(f"conflict:1:{SHA_A}", "acme/app", "1")], [tuple(row[:3]) for row in self.state_rows()])

    def test_an_unsettled_mergeability_is_not_a_conflict(self) -> None:
        self.pull_request(1, mergeable="UNKNOWN")
        self.track("acme/app#1")

        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)
        self.assertEqual([], self.state_rows())

    def test_a_merged_pull_request_falls_silent_and_out_of_the_state(self) -> None:
        self.pull_request(1, draft=True, threads=[thread("T1")])
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(THREADS))
        self.assertEqual(2, len(self.state_rows()))

        self.pull_request(1, state="MERGED", draft=True, threads=[thread("T1")])
        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)
        self.assertEqual([], self.state_rows())

    def test_exit_when_closed_ends_the_watch_once_every_tracked_pull_request_closed(self) -> None:
        self.pull_request(1, state="MERGED")
        self.pull_request(2, state="CLOSED")
        self.track("acme/app#1", "acme/app#2")

        result = self.run_watcher(THREADS, "--exit-when-closed")

        self.assert_ok(result)
        self.assertEqual([DONE], result.stdout.splitlines())

    def test_exit_when_closed_keeps_watching_while_one_is_open(self) -> None:
        self.pull_request(1, state="MERGED")
        self.pull_request(2)
        self.track("acme/app#1", "acme/app#2")

        result = self.run_watcher(THREADS, "--exit-when-closed")

        self.assert_ok(result)
        self.assertEqual("", result.stdout)

    def test_exit_when_closed_ends_a_watch_whose_file_names_none(self) -> None:
        self.tracked.write_text("# every pull request merged\n")

        result = self.run_watcher(THREADS, "--exit-when-closed")

        self.assert_ok(result)
        self.assertEqual([DONE], result.stdout.splitlines())

    def test_exit_when_closed_never_ends_on_a_failed_query(self) -> None:
        self.pull_request(1, state="MERGED")
        self.track("acme/app#1")

        # One entry costs one verification call; every call after it fails.
        result = self.run_watcher(THREADS, "--exit-when-closed", down_after=1)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)

    def test_exit_when_closed_never_ends_on_a_bad_tracked_line(self) -> None:
        self.pull_request(1, state="MERGED")
        self.track("acme/app#1")

        # The line arrives after the check before the watch, as one the loop
        # appends to a running watch's file would.
        result = self.run_watcher(THREADS, "--exit-when-closed", late_line="2013")

        self.assert_ok(result)
        self.assertEqual([f"TRACKED {self.tracked}:2 is not owner/repo#number: 2013"], result.stdout.splitlines())

    def test_exit_when_closed_stops_a_running_watch(self) -> None:
        self.pull_request(1, state="MERGED")
        self.track("acme/app#1")

        result = subprocess.run(
            ["bash", str(THREADS), "--tracked", str(self.tracked), "--state", str(self.state), "--exit-when-closed"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            timeout=30,
        )

        self.assert_ok(result)
        self.assertEqual([DONE], result.stdout.splitlines())

    def test_the_tracked_file_is_read_again_on_every_pass(self) -> None:
        self.pull_request(1, threads=[thread("T1")])
        self.pull_request(2, threads=[thread("T2", body="P3 typo")])
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(THREADS))

        self.track("acme/app#1", "acme/app#2")
        added = self.run_watcher(THREADS)
        self.track("acme/app#2")
        removed = self.run_watcher(THREADS)

        self.assert_ok(added)
        self.assertEqual(["FINDING acme/app#2 src/x.py:3 [bugbot] — P3 typo"], added.stdout.splitlines())
        self.assert_ok(removed)
        self.assertEqual("", removed.stdout)
        self.assertEqual([("T2", "acme/app", "2")], [tuple(row[:3]) for row in self.state_rows()])

    def test_carries_rows_forward_when_a_query_fails_after_startup(self) -> None:
        self.pull_request(1, threads=[thread("T1")])
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(THREADS))
        before = self.state.read_text()
        self.assertIn("T1 acme/app 1 ", before)

        # One entry costs one verification call; every call after it fails.
        result = self.run_watcher(THREADS, down_after=1)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)
        self.assertEqual(before, self.state.read_text())

    def test_refuses_a_line_that_is_not_owner_repo_number(self) -> None:
        self.pull_request(1)
        self.tracked.write_text("acme/app#1\n2013\n")

        result = self.run_watcher(THREADS)

        self.assertEqual(2, result.returncode)
        self.assertIn(f"{self.tracked}:2 is not owner/repo#number: 2013", result.stderr)
        self.assertEqual("", result.stdout)
        self.assertEqual("", self.calls())

    def test_an_exclusion_keeps_a_pull_request_out_wherever_it_sits_and_in_any_letter_case(self) -> None:
        self.pull_request(1, threads=[thread("T1", body="P2 off by one")])
        self.pull_request(2, threads=[thread("T2", body="P3 typo")])
        # The exclusion `project loop drop` would append, here above the line it excludes.
        self.tracked.write_text("!ACME/App#2\nacme/app#1\nacme/app#2\n")

        result = self.run_watcher(THREADS)

        self.assert_ok(result)
        self.assertEqual(["FINDING acme/app#1 src/x.py:3 [bugbot] — P2 off by one"], result.stdout.splitlines())
        self.assertNotIn(" n=2 ", self.calls())
        self.assertNotIn("pulls/2/", self.calls())

    def test_refuses_a_number_that_names_no_pull_request(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1", "acme/app#7")

        result = self.run_watcher(THREADS)

        self.assertEqual(2, result.returncode)
        self.assertIn("acme/app#7: no such repository or pull request", result.stderr)
        self.assertEqual("", result.stdout)

    def test_refuses_a_misspelled_repository_as_missing_rather_than_unreachable(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1", "acme/ap#1")

        result = self.run_watcher(THREADS)

        self.assertEqual(2, result.returncode)
        self.assertIn("acme/ap#1: no such repository or pull request", result.stderr)
        self.assertNotIn("could not verify", result.stderr)
        self.assertEqual("", result.stdout)

    def test_refuses_to_start_when_github_cannot_be_reached(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1")

        result = self.run_watcher(THREADS, down_after=0)

        self.assertEqual(2, result.returncode)
        self.assertIn("could not verify — network or auth error, not a missing repository or pull request", result.stderr)
        self.assertNotIn("no such", result.stderr)

    def test_refuses_a_last_flag_without_its_value(self) -> None:
        # A short timeout, so a watcher that loops on the missing value fails the test instead of hanging it.
        for flag in ("--tracked", "--state", "--interval", "--renotify"):
            result = self.run_watcher(THREADS, flag, timeout=10)

            self.assertEqual(2, result.returncode, flag)
            self.assertEqual(f"watch-threads.sh: {flag} needs a value\n", result.stderr)

    def test_refuses_the_repository_and_author_flags(self) -> None:
        for flag in ("--repos", "--author"):
            with self.subTest(flag=flag):
                result = self.run_watcher(THREADS, flag, "acme/app")
                self.assertEqual(2, result.returncode)
                self.assertIn(f"unknown argument: {flag}", result.stderr)


class WatchPrsTests(WatcherCase):
    def test_events_come_only_from_tracked_pull_requests(self) -> None:
        self.pull_request(1, ref="feature-a")
        self.pull_request(2, ref="feature-b")
        self.track("acme/app#1")

        result = self.run_watcher(PRS)

        self.assert_ok(result)
        self.assertEqual(
            ["NEW PR acme/app#1 (feature-a) head=aaaaaaa — unreviewed, needs an exact-head review"],
            result.stdout.splitlines(),
        )
        self.assertNotIn(" n=2 ", self.calls())
        self.assertEqual([["acme/app", "1", SHA_A, "feature-a", f"{SHA_A}:-"]], self.state_rows())

    def test_reports_a_moved_head_then_a_response_once_per_head(self) -> None:
        self.pull_request(1, head=SHA_A, draft=True)
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_B} feature\n")

        # The head is announced, so a draft with nothing open is not yet a
        # response: the review of that head has not happened.
        moved = self.run_watcher(PRS)
        suppressed = self.run_watcher(PRS)
        # The review lands findings, the author resolves them without pushing.
        self.pull_request(1, head=SHA_A, draft=True, threads=[thread("T1")])
        under_review = self.run_watcher(PRS)
        self.pull_request(1, head=SHA_A, draft=True, threads=[thread("T1", resolved=True)])
        responded = self.run_watcher(PRS)
        quiet = self.run_watcher(PRS)
        # A cross-author changes request answered the same way.
        self.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)], threads=[thread("T2")])
        reopened = self.run_watcher(PRS)
        after_reopened = self.state_rows()
        self.pull_request(1, head=SHA_A, verdicts=[projector_verdict(SHA_A)], threads=[thread("T2", resolved=True)])
        cross_author = self.run_watcher(PRS)

        self.assert_ok(moved)
        self.assertEqual(
            ["NEW HEAD acme/app#1 (feature): bbbbbbb -> aaaaaaa — needs an exact-head review"],
            moved.stdout.splitlines(),
        )
        self.assert_ok(suppressed)
        self.assertEqual("", suppressed.stdout)
        self.assert_ok(under_review)
        self.assertEqual("", under_review.stdout)
        self.assert_ok(responded)
        self.assertEqual(
            [
                "RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft with every thread "
                "resolved; re-review this head and sign off if clean"
            ],
            responded.stdout.splitlines(),
        )
        self.assert_ok(quiet)
        self.assertEqual("", quiet.stdout)
        self.assert_ok(reopened)
        self.assertEqual("", reopened.stdout)
        self.assertEqual([["acme/app", "1", SHA_A, "feature", "-"]], after_reopened)
        self.assert_ok(cross_author)
        self.assertEqual(
            [
                "RESPONDED acme/app#1 (feature) head=aaaaaaa — changes requested with every thread "
                "resolved; re-review this head, and a clean one is a COMMENT, never an approval"
            ],
            cross_author.stdout.splitlines(),
        )

    def test_reports_an_author_reply_on_an_open_finding_once_per_reply(self) -> None:
        # A person who declines a finding in a reply, without resolving it or
        # pushing, is waiting on the reviewer just as surely as one who resolves.
        finding = {"databaseId": 11, "body": "<!-- projector-finding v=1 priority=P2 --> A nil deref"}

        def finding_thread(*replies):
            node = thread("T1", body=finding["body"])
            node["comments"]["nodes"] = [finding, *replies]
            return node

        self.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread()])
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_A} feature {SHA_A}\n")

        unanswered = self.run_watcher(PRS)
        decline = {"databaseId": 12, "body": "<!-- projector-reply v=1 --> Declined: the caller checks for nil."}
        self.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread(decline)])
        replied = self.run_watcher(PRS)
        quiet = self.run_watcher(PRS)
        kept = {"databaseId": 13, "body": "<!-- projector-verify v=1 result=kept --> The second caller does not."}
        self.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread(decline, kept)])
        after_verify = self.run_watcher(PRS)
        again = {"databaseId": 14, "body": "Fair, but that caller is dead code."}
        self.pull_request(1, head=SHA_A, draft=True, threads=[finding_thread(decline, kept, again)])
        replied_again = self.run_watcher(PRS)

        self.assert_ok(unanswered)
        self.assertEqual("", unanswered.stdout, "the older state form of an unanswered head stays quiet")
        self.assert_ok(replied)
        self.assertEqual(
            ["RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft and the author replied on an open "
             "finding; re-review this head and sign off if clean"],
            replied.stdout.splitlines(),
        )
        self.assert_ok(quiet)
        self.assertEqual("", quiet.stdout)
        self.assert_ok(after_verify)
        self.assertEqual("", after_verify.stdout, "the reviewer's own verify reply is not an answer")
        self.assert_ok(replied_again)
        self.assertEqual(1, len(replied_again.stdout.splitlines()))
        self.assertIn("the author replied on an open finding", replied_again.stdout)

    def test_resolving_a_replied_finding_after_a_push_does_not_re_request_the_head(self) -> None:
        # The watch loop replies, pushes, then resolves; a poll between the push
        # and the resolve announces the head with a reply outstanding. Resolving
        # it next, or the reviewer resolving it during that head's review, must
        # not ask for the same head again.
        finding = {"databaseId": 31, "body": "<!-- projector-finding v=1 priority=P2 --> A nil deref"}
        reply = {"databaseId": 32, "body": "<!-- projector-reply v=1 --> Fixed in aaaaaaa."}
        node = thread("T1", body=finding["body"])
        node["comments"]["nodes"] = [finding, reply]
        self.pull_request(1, head=SHA_A, draft=True, threads=[node])
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_B} feature\n")

        moved = self.run_watcher(PRS)
        node = dict(node, isResolved=True)
        self.pull_request(1, head=SHA_A, draft=True, threads=[node])
        resolved = self.run_watcher(PRS)

        self.assert_ok(moved)
        self.assertEqual(["NEW HEAD acme/app#1 (feature): bbbbbbb -> aaaaaaa — needs an exact-head review"],
                         moved.stdout.splitlines())
        self.assert_ok(resolved)
        self.assertEqual("", resolved.stdout)

    def test_a_reply_on_another_reviewers_thread_is_not_a_response(self) -> None:
        human = thread("T1", who="alice", body="Should this be a constant?")
        human["comments"]["nodes"].append({"databaseId": 21, "author": {"login": "operator"}, "body": "Done."})
        self.pull_request(1, head=SHA_A, draft=True, threads=[human])
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_A} feature\n")

        result = self.run_watcher(PRS)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)

    def test_a_hold_is_never_a_response_and_lifting_it_is_one(self) -> None:
        # fix-pr returns a signed-off pull request to draft while it builds a
        # change. A re-review then would only find the unchanged head clean.
        # It posts the hold while the pull request is still ready, then drafts
        # it, so no pass sees a draft without the hold.
        hold = {"author": {"login": "operator"},
                "body": "Held in draft while the following changes are implemented:\n\n- Rename `x`\n\n"
                        "<!-- projector-hold v=1 -->\n"}
        self.pull_request(1, head=SHA_A)
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_A} feature -\n")

        self.pull_request(1, head=SHA_A, comments=[hold])
        posted = self.run_watcher(PRS)
        held_flag = self.state_rows()
        self.pull_request(1, head=SHA_A, draft=True, comments=[hold])
        held = self.run_watcher(PRS)
        still_held = self.run_watcher(PRS)
        self.pull_request(1, head=SHA_A, draft=True)
        lifted = self.run_watcher(PRS)
        quiet = self.run_watcher(PRS)

        self.assert_ok(posted)
        self.assertEqual("", posted.stdout)
        self.assertEqual([["acme/app", "1", SHA_A, "feature", "held"]], held_flag)
        self.assert_ok(held)
        self.assertEqual("", held.stdout)
        self.assert_ok(still_held)
        self.assertEqual("", still_held.stdout)
        self.assert_ok(lifted)
        self.assertEqual(
            ["RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft now that its hold is lifted; "
             "re-review this head and sign off if clean"],
            lifted.stdout.splitlines(),
        )
        self.assert_ok(quiet)
        self.assertEqual("", quiet.stdout)

    def test_drafting_before_the_hold_lands_asks_to_re_review_the_unchanged_head(self) -> None:
        # Why fix-pr posts the hold before it drafts: a pass between the two
        # steps would read an author who answered without pushing.
        self.pull_request(1, head=SHA_A)
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_A} feature -\n")

        self.pull_request(1, head=SHA_A, draft=True)
        drafted_first = self.run_watcher(PRS)

        self.assert_ok(drafted_first)
        self.assertEqual(
            ["RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft with every thread resolved; "
             "re-review this head and sign off if clean"],
            drafted_first.stdout.splitlines(),
        )

    def test_a_hold_seen_on_a_new_head_asks_for_it_again_once_lifted(self) -> None:
        # The push that carries the changes can land a cycle before the hold
        # is deleted, and that head's review then leaves it a draft.
        hold = {"author": {"login": "operator"}, "body": "Held\n\n- Rename `x`\n\n<!-- projector-hold v=1 -->"}
        self.pull_request(1, head=SHA_A, draft=True, comments=[hold])
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_B} feature -\n")

        moved = self.run_watcher(PRS)
        self.pull_request(1, head=SHA_A, draft=True)
        lifted = self.run_watcher(PRS)

        self.assert_ok(moved)
        self.assertEqual(["NEW HEAD acme/app#1 (feature): bbbbbbb -> aaaaaaa — needs an exact-head review"],
                         moved.stdout.splitlines())
        self.assert_ok(lifted)
        self.assertEqual(1, len(lifted.stdout.splitlines()), lifted.stdout)
        self.assertIn("still a draft now that its hold is lifted", lifted.stdout)

    def test_a_hold_marker_from_anyone_but_the_author_holds_nothing(self) -> None:
        planted = {"author": {"login": "passerby"}, "body": "<!-- projector-hold v=1 -->"}
        self.pull_request(1, head=SHA_A, draft=True, comments=[planted])
        self.track("acme/app#1")
        self.state.write_text(f"acme/app 1 {SHA_A} feature -\n")

        result = self.run_watcher(PRS)

        self.assert_ok(result)
        self.assertEqual(
            ["RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft with every thread resolved; "
             "re-review this head and sign off if clean"],
            result.stdout.splitlines(),
        )

    def test_a_head_this_reviewer_already_reviewed_takes_its_row_without_a_word(self) -> None:
        self.pull_request(1, ref="reviewed", verdicts=[projector_verdict(SHA_A, "clean")])
        self.pull_request(2, ref="other-model", verdicts=[projector_verdict(SHA_A, "clean", model="gpt-6")])
        self.pull_request(3, ref="unreviewed")
        self.pull_request(4, ref="older-head", verdicts=[projector_verdict(SHA_B, "clean")])
        self.pull_request(5, ref="no-model", verdicts=[projector_verdict(SHA_A, "clean", model=None)])
        self.track(*(f"acme/app#{n}" for n in range(1, 6)))
        reviewer = ("--reviewer", "operator")

        without_model = self.run_watcher(PRS)
        self.state.unlink()
        # The model ID the host reports, with the context-window variant `project review` ignores, and the
        # login in another case.
        with_model = self.run_watcher(PRS, "--model", "Claude-Opus-5-5[1m]", "--reviewer", "Operator")
        again = self.run_watcher(PRS, "--model", "claude-opus-5-5", *reviewer)

        def new_pr(n, ref):
            return f"NEW PR acme/app#{n} ({ref}) head=aaaaaaa — unreviewed, needs an exact-head review"

        self.assert_ok(without_model)
        self.assertEqual(5, len(without_model.stdout.splitlines()), without_model.stdout)
        self.assertIn(new_pr(1, "reviewed"), without_model.stdout)
        self.assert_ok(with_model)
        self.assertEqual(
            [new_pr(2, "other-model"), new_pr(3, "unreviewed"), new_pr(4, "older-head"), new_pr(5, "no-model")],
            with_model.stdout.splitlines(),
        )
        self.assertEqual(["acme/app", "1", SHA_A, "reviewed", "-"], self.state_rows()[0])
        self.assertEqual(5, len(self.state_rows()))
        self.assert_ok(again)
        self.assertEqual("", again.stdout)

    def test_only_the_reviewers_own_verdict_takes_a_row_without_a_word(self) -> None:
        # A teammate whose own loop runs the same model reviewed, then asked for the operator's review.
        self.pull_request(1, ref="teammate", author="teammate", requested=("operator",),
                          verdicts=[projector_verdict(SHA_A, "clean", who="teammate")])
        self.pull_request(2, ref="passerby", author="teammate", requested=("operator",),
                          verdicts=[projector_verdict(SHA_A, "clean", who="passerby", association="NONE")])
        # The reviewer's own verdict counts whatever its association, as `project review status` counts it.
        self.pull_request(3, ref="outside-reviewer", author="teammate", requested=("operator",),
                          verdicts=[projector_verdict(SHA_A, "clean", association="NONE")])

        result = self.run_watcher(PRS, "--repo", "acme/app", "--review-requested", "operator",
                                  "--model", "claude-opus-5-5", "--reviewer", "operator")

        self.assert_ok(result)
        self.assertEqual(
            ["NEW PR acme/app#1 (teammate) head=aaaaaaa — unreviewed, needs an exact-head review",
             "NEW PR acme/app#2 (passerby) head=aaaaaaa — unreviewed, needs an exact-head review"],
            result.stdout.splitlines(),
        )
        self.assertEqual([["acme/app", "3", SHA_A, "outside-reviewer", "-"]],
                         [row for row in self.state_rows() if row[1] == "3"])

    def test_a_reviewed_head_whose_author_answered_asks_for_a_re_review(self) -> None:
        # The author resolved every thread of this model's review without
        # pushing while no watcher ran, so no head event is coming.
        self.pull_request(1, draft=True, verdicts=[projector_verdict(SHA_A)], threads=[thread("T1", resolved=True)])
        self.track("acme/app#1")

        baselined = self.run_watcher(PRS, "--model", "claude-opus-5-5", "--reviewer", "operator")
        quiet = self.run_watcher(PRS, "--model", "claude-opus-5-5", "--reviewer", "operator")

        self.assert_ok(baselined)
        self.assertEqual(
            ["RESPONDED acme/app#1 (feature) head=aaaaaaa — still a draft with every thread resolved; "
             "re-review this head and sign off if clean"],
            baselined.stdout.splitlines(),
        )
        self.assert_ok(quiet)
        self.assertEqual("", quiet.stdout)

    def test_the_model_leaves_a_pull_request_with_a_row_to_its_row(self) -> None:
        reviewed = [projector_verdict(SHA_A, "clean")]
        self.pull_request(1, ref="moved", verdicts=reviewed)
        self.pull_request(2, ref="seen", verdicts=reviewed)
        self.track("acme/app#1", "acme/app#2")
        self.state.write_text(f"acme/app 1 {SHA_B} moved -\nacme/app 2 {SHA_A} seen -\n")

        result = self.run_watcher(PRS, "--model", "claude-opus-5-5", "--reviewer", "operator")

        self.assert_ok(result)
        self.assertEqual(["NEW HEAD acme/app#1 (moved): bbbbbbb -> aaaaaaa — needs an exact-head review"],
                         result.stdout.splitlines())
        self.assertEqual([["acme/app", "1", SHA_A, "moved", f"{SHA_A}:-"], ["acme/app", "2", SHA_A, "seen", "-"]],
                         self.state_rows())

    def test_reports_a_tracked_pull_request_that_closed_once(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(PRS))

        self.pull_request(1, state="MERGED")
        closed = self.run_watcher(PRS)
        again = self.run_watcher(PRS)

        self.assert_ok(closed)
        self.assertEqual(
            ["CLOSED acme/app#1 (feature) — no longer open; drop it from the tracked file"],
            closed.stdout.splitlines(),
        )
        self.assertEqual([], self.state_rows())
        self.assert_ok(again)
        self.assertEqual("", again.stdout)

    def test_drops_a_pull_request_removed_from_the_file_without_a_word(self) -> None:
        self.pull_request(1)
        self.pull_request(2)
        self.track("acme/app#1", "acme/app#2")
        both = self.run_watcher(PRS)

        self.track("acme/app#1")
        result = self.run_watcher(PRS)

        self.assert_ok(both)
        self.assertEqual(2, len(both.stdout.splitlines()), both.stdout)
        self.assert_ok(result)
        self.assertEqual("", result.stdout)
        self.assertEqual([["acme/app", "1", SHA_A, "feature", "-"]], self.state_rows())

    def test_carries_a_row_forward_when_a_query_fails_after_startup(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1")
        self.assert_ok(self.run_watcher(PRS))
        before = self.state.read_text()

        result = self.run_watcher(PRS, down_after=1)

        self.assert_ok(result)
        self.assertEqual("", result.stdout)
        self.assertEqual(before, self.state.read_text())

    def test_refuses_a_bad_tracked_file_and_the_old_flags(self) -> None:
        self.pull_request(1)
        self.tracked.write_text("acme/app#1\nacme/app 2\n")
        malformed = self.run_watcher(PRS)
        self.track("acme/app#1", "acme/app#7")
        missing = self.run_watcher(PRS)
        self.track("acme/app#1", "acme/ap#1")
        misspelled = self.run_watcher(PRS)
        self.track("acme/app#1")
        unreachable = self.run_watcher(PRS, down_after=0)
        author = self.run_watcher(PRS, "--author", "someone")
        repo_alone = self.run_watcher(PRS, "--repo", "acme/app")
        not_a_login = self.run_watcher(PRS, "--repo", "acme/app", "--review-requested", "ada is:merged")

        self.assertEqual(2, malformed.returncode)
        self.assertIn(f"{self.tracked}:2 is not owner/repo#number: acme/app 2", malformed.stderr)
        self.assertEqual(2, missing.returncode)
        self.assertIn("acme/app#7: no such repository or pull request", missing.stderr)
        self.assertEqual(2, misspelled.returncode)
        self.assertIn("acme/ap#1: no such repository or pull request", misspelled.stderr)
        self.assertNotIn("could not verify", misspelled.stderr)
        self.assertEqual(2, unreachable.returncode)
        self.assertIn("could not verify — network or auth error", unreachable.stderr)
        for alone in (author, repo_alone):
            self.assertEqual(2, alone.returncode)
            self.assertIn("--repo goes with --author, --review-requested, or both", alone.stderr)
        self.assertEqual(2, not_a_login.returncode)
        self.assertIn("--review-requested is not a GitHub login: ada is:merged", not_a_login.stderr)

    def test_refuses_a_last_flag_without_its_value(self) -> None:
        # A short timeout, so a watcher that loops on the missing value fails the test instead of hanging it.
        for flag in ("--tracked", "--state", "--interval", "--worktree", "--repo", "--author",
                     "--review-requested", "--model", "--reviewer"):
            result = self.run_watcher(PRS, flag, timeout=10)

            self.assertEqual(2, result.returncode, flag)
            self.assertEqual(f"watch-prs.sh: {flag} needs a value\n", result.stderr)

    def test_refuses_a_reviewer_that_no_verdict_could_name(self) -> None:
        self.pull_request(1)
        self.track("acme/app#1")
        refusals = [(("--model", "claude-opus-5-5"), "--model goes with --reviewer"),
                    (("--reviewer", "operator"), "--model goes with --reviewer"),
                    (("--model", "claude-opus-5-5", "--reviewer", "ada lovelace"),
                     "--reviewer is not a GitHub login: ada lovelace")]
        refusals += [(("--model", model, "--reviewer", "operator"), f"--model is not a model ID: {model}")
                     for model in ("-", "a,b", "claude opus")]

        for args, message in refusals:
            refused = self.run_watcher(PRS, *args)

            self.assertEqual(2, refused.returncode, args)
            self.assertIn(message, refused.stderr)
            self.assertEqual("", refused.stdout)

    def test_adopts_the_authors_open_pull_requests(self) -> None:
        self.pull_request(1, ref="mine")
        self.pull_request(2, ref="theirs", author="teammate")
        self.pull_request(3, state="MERGED")
        self.pull_request(4, ref="excluded")
        self.pull_request(5, ref="tracked")
        self.pull_request(6, ref="later")
        # No trailing newline, so an append has to start a line of its own.
        self.tracked.write_text("!acme/app#4\n  acme/app#5  ")
        adopt = ("--repo", "acme/app", "--author", "operator")

        first = self.run_watcher(PRS, *adopt)
        second = self.run_watcher(PRS, *adopt)

        self.assert_ok(first)
        self.assertEqual(
            [
                "NEW PR acme/app#5 (tracked) head=aaaaaaa — unreviewed, needs an exact-head review",
                "NEW PR acme/app#1 (mine) head=aaaaaaa — unreviewed, needs an exact-head review",
                "NEW PR acme/app#6 (later) head=aaaaaaa — unreviewed, needs an exact-head review",
            ],
            first.stdout.splitlines(),
        )
        self.assertEqual("!acme/app#4\n  acme/app#5  \nacme/app#1\nacme/app#6\n", self.tracked.read_text())
        self.assert_ok(second)
        self.assertEqual("", second.stdout)
        self.assertEqual(4, len(self.tracked.read_text().splitlines()))

    def test_adopts_the_pull_requests_that_request_the_logins_review(self) -> None:
        self.pull_request(1, ref="mine")
        self.pull_request(2, ref="asked", author="teammate", requested=("ada", "operator"))
        self.pull_request(3, ref="asked-elsewhere", author="teammate", requested=("ada",))
        self.pull_request(4, ref="merged", author="teammate", requested=("operator",), state="MERGED")
        self.pull_request(5, ref="unasked", author="teammate")

        requested = self.run_watcher(PRS, "--repo", "acme/app", "--review-requested", "operator")
        requested_calls = self.calls()
        both = self.run_watcher(PRS, "--repo", "acme/app", "--author", "operator", "--review-requested", "operator")
        # The operator reviewed, which drops the request, and the author pushed.
        self.pull_request(2, ref="asked", author="teammate", head=SHA_B)
        after_review = self.run_watcher(PRS, "--repo", "acme/app", "--author", "operator", "--review-requested", "operator")

        self.assert_ok(requested)
        self.assertEqual(
            ["NEW PR acme/app#2 (asked) head=aaaaaaa — unreviewed, needs an exact-head review"],
            requested.stdout.splitlines(),
        )
        self.assertIn("--search user-review-requested:operator", requested_calls)
        self.assertNotIn("--author", requested_calls)
        self.assert_ok(both)
        self.assertEqual(
            ["NEW PR acme/app#1 (mine) head=aaaaaaa — unreviewed, needs an exact-head review"],
            both.stdout.splitlines(),
        )
        self.assert_ok(after_review)
        self.assertEqual(
            ["NEW HEAD acme/app#2 (asked): aaaaaaa -> bbbbbbb — needs an exact-head review"],
            after_review.stdout.splitlines(),
        )
        self.assertEqual("acme/app#2\nacme/app#1\n", self.tracked.read_text())

    def test_only_the_authors_own_draft_waits_on_a_clean_review(self) -> None:
        # A review never moves another author's draft state, so that draft stays
        # a draft after a clean review dismisses the change request it settled.
        adopt = ("--repo", "acme/app", "--author", "operator", "--review-requested", "operator")

        def both(**node):
            self.pull_request(1, ref="mine", draft=True, **node)
            self.pull_request(2, ref="asked", author="teammate", draft=True, **node)

        both(requested=("operator",))
        adopted = self.run_watcher(PRS, *adopt)
        # The review requests changes, the authors push a fix, and that head's
        # review starts with the finding still open.
        changes = projector_verdict(SHA_A)
        both(verdicts=[changes], threads=[thread("T1")])
        reviewed = self.run_watcher(PRS, *adopt)
        both(head=SHA_B, verdicts=[changes], threads=[thread("T1")])
        pushed = self.run_watcher(PRS, *adopt)
        under_review = self.run_watcher(PRS, *adopt)
        # It resolves the finding and gives a clean verdict.
        clean = projector_verdict(SHA_B, "clean", at="2026-10-12T11:00:00Z")
        both(head=SHA_B, verdicts=[changes, clean], threads=[thread("T1", resolved=True)])
        settled = self.run_watcher(PRS, *adopt)

        self.assert_ok(adopted)
        self.assertEqual(2, len(adopted.stdout.splitlines()), adopted.stdout)
        self.assert_ok(reviewed)
        self.assertEqual("", reviewed.stdout)
        self.assert_ok(pushed)
        self.assertEqual(2, len(pushed.stdout.splitlines()), pushed.stdout)
        self.assert_ok(under_review)
        self.assertEqual("", under_review.stdout)
        self.assert_ok(settled)
        self.assertEqual(
            ["RESPONDED acme/app#1 (mine) head=bbbbbbb — still a draft with every thread resolved; "
             "re-review this head and sign off if clean"],
            settled.stdout.splitlines(),
        )

    def test_another_authors_answer_without_a_push_asks_for_a_re_review(self) -> None:
        # GitHub leaves reviewDecision null where the base branch requires no
        # reviews, so the verdict marker is what says the head waits on an answer.
        finding = {"databaseId": 11, "body": "<!-- projector-finding v=1 priority=P2 --> A nil deref"}
        reply = {"databaseId": 12, "body": "<!-- projector-reply v=1 --> Declined: the caller checks for nil."}

        def finding_thread(*replies, resolved=False):
            node = thread("T1", body=finding["body"], resolved=resolved)
            node["comments"]["nodes"] = [finding, *replies]
            return node

        def teammates(threads, outsider_threads):
            changes = projector_verdict(SHA_A)
            self.pull_request(2, ref="draft", author="teammate", draft=True, verdicts=[changes], threads=threads)
            self.pull_request(3, ref="ready", author="teammate", verdicts=[changes], threads=outsider_threads)
            # Anyone can review a public repository, so a passerby's marker is no verdict there.
            self.pull_request(4, ref="passerby", author="teammate", threads=outsider_threads,
                              verdicts=[projector_verdict(SHA_A, association="NONE")])
            # In a private one, every review counts.
            self.pull_request(5, repo="acme/secret", ref="private", author="teammate", threads=outsider_threads,
                              verdicts=[projector_verdict(SHA_A, association="NONE")])

        (self.fixtures / "acme/secret").mkdir(parents=True)
        (self.fixtures / "acme/secret/visibility").write_text("PRIVATE")
        self.track("acme/app#2", "acme/app#3", "acme/app#4", "acme/secret#5")
        self.state.write_text("".join(f"{slug} {n} {SHA_A} {ref} -\n" for slug, n, ref in (
            ("acme/app", 2, "draft"), ("acme/app", 3, "ready"), ("acme/app", 4, "passerby"),
            ("acme/secret", 5, "private"))))
        teammates([finding_thread()], [finding_thread()])
        reviewed = self.run_watcher(PRS, "--repo", "acme/app", "--author", "operator")
        # Without a push, one author replies on the finding and the others resolve it.
        teammates([finding_thread(reply)], [finding_thread(resolved=True)])
        answered = self.run_watcher(PRS, "--repo", "acme/app", "--author", "operator")

        self.assert_ok(reviewed)
        self.assertEqual("", reviewed.stdout)
        self.assert_ok(answered)
        cross_author = "re-review this head, and a clean one is a COMMENT, never an approval"
        self.assertEqual(
            [
                f"RESPONDED acme/app#2 (draft) head=aaaaaaa — changes requested and the author replied on an "
                f"open finding; {cross_author}",
                f"RESPONDED acme/app#3 (ready) head=aaaaaaa — changes requested with every thread resolved; "
                f"{cross_author}",
                f"RESPONDED acme/secret#5 (private) head=aaaaaaa — changes requested with every thread resolved; "
                f"{cross_author}",
            ],
            answered.stdout.splitlines(),
        )

    def test_a_review_that_settles_findings_before_it_publishes_is_no_answer(self) -> None:
        # The review of a pushed head resolves the findings it verifies before
        # it gives its verdict, and on a branch that requires reviews the change
        # request stands until that verdict dismisses it. The verdict on the
        # earlier head is no longer the head's, so a pass in between does not
        # take the author to have answered.
        changes = projector_verdict(SHA_A)

        def asked(head, verdicts, threads, decision="CHANGES_REQUESTED"):
            self.pull_request(2, ref="asked", author="teammate", head=head, decision=decision,
                              verdicts=verdicts, threads=threads)

        asked(SHA_A, [changes], [thread("T1")])
        self.track("acme/app#2")
        self.state.write_text(f"acme/app 2 {SHA_A} asked -\n")

        asked(SHA_B, [changes], [thread("T1")])
        pushed = self.run_watcher(PRS)
        under_review = self.run_watcher(PRS)
        resolved = [thread("T1", resolved=True)]
        asked(SHA_B, [changes], resolved)
        settled = self.run_watcher(PRS)
        clean = projector_verdict(SHA_B, "clean", at="2026-10-12T11:00:00Z")
        asked(SHA_B, [changes, clean], resolved, decision="REVIEW_REQUIRED")
        published = self.run_watcher(PRS)

        self.assert_ok(pushed)
        self.assertEqual(["NEW HEAD acme/app#2 (asked): aaaaaaa -> bbbbbbb — needs an exact-head review"],
                         pushed.stdout.splitlines())
        for quiet in (under_review, settled, published):
            self.assert_ok(quiet)
            self.assertEqual("", quiet.stdout)

    def test_an_exclusion_holds_after_adoption_and_in_any_letter_case(self) -> None:
        self.pull_request(4, ref="excluded")
        self.pull_request(5, ref="kept")
        # The plain line adoption wrote, then an exclusion added below it, and a
        # second spelling of an entry the file already names.
        self.tracked.write_text("acme/app#4\nacme/app#5\n!ACME/app#4\nAcme/App#5\n")

        adopted = self.run_watcher(PRS, "--repo", "Acme/App", "--author", "operator")

        self.assert_ok(adopted)
        self.assertEqual(
            ["NEW PR acme/app#5 (kept) head=aaaaaaa — unreviewed, needs an exact-head review"],
            adopted.stdout.splitlines(),
        )
        self.assertEqual("acme/app#4\nacme/app#5\n!ACME/app#4\nAcme/App#5\n", self.tracked.read_text())

    def test_adoption_starts_from_no_tracked_file_and_survives_a_failed_listing(self) -> None:
        self.pull_request(1)
        self.tracked.unlink()
        adopt = ("--repo", "acme/app", "--author", "operator")

        failed = self.run_watcher(PRS, *adopt, down_after=0)
        after_failure = self.tracked.read_text()
        recovered = self.run_watcher(PRS, *adopt)

        self.assert_ok(failed)
        self.assertEqual("", failed.stdout)
        self.assertEqual("", after_failure)
        self.assert_ok(recovered)
        self.assertEqual(
            ["NEW PR acme/app#1 (feature) head=aaaaaaa — unreviewed, needs an exact-head review"],
            recovered.stdout.splitlines(),
        )


if __name__ == "__main__":
    unittest.main()
