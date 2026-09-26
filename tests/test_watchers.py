"""Drive both watcher scripts through a fake ``gh`` and check what they say.

The fake serves one fixture per pull request and applies every ``--jq``
filter with the system ``jq``, so the scripts' own filters are what run. Each
test makes single passes with ``--once``; the state file carries what one pass
announced into the next, exactly as it does between cycles of a live watch.
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
THREADS = ROOT / "skills" / "start-fix-loop" / "scripts" / "watch-threads.sh"
PRS = ROOT / "skills" / "start-review-loop" / "scripts" / "watch-prs.sh"

SHA_A = "a" * 40
SHA_B = "b" * 40

FAKE_GH = r"""#!/usr/bin/env bash
# Serves fixtures from $FAKE_GH_DIR:
#   <owner>/<repo>/<n>.json          the GraphQL pullRequest node; absent means NOT_FOUND
#   <owner>/<repo>/<n>.reviews.json  the REST reviews array; absent means []
# `pr list --repo <owner>/<repo> --author <login>` lists the open fixtures whose
# node names that author.
# Logs one line per call to $FAKE_GH_DIR/calls.log. With FAKE_GH_DOWN_AFTER set,
# every call past that many already-logged calls fails like a network error.
set -u
log="$FAKE_GH_DIR/calls.log"
count=0
[ -f "$log" ] && count=$(wc -l < "$log" | tr -d ' ')
printf '%s\n' "$*" >> "$log"
if [ -n "${FAKE_GH_DOWN_AFTER:-}" ] && [ "$count" -ge "$FAKE_GH_DOWN_AFTER" ]; then
  echo 'Post "https://api.github.com/graphql": dial tcp 127.0.0.1:9: connect: connection refused' >&2
  exit 1
fi
mode=""; owner=""; repo=""; num=""; jq_expr="."; path=""; slug=""; author=""
while [ $# -gt 0 ]; do
  case "$1" in
    api) shift ;;
    pr) mode=list; shift 2 ;;
    --repo) slug="$2"; shift 2 ;;
    --author) author="$2"; shift 2 ;;
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
      jq -c --arg a "$author" --argjson n "$(basename "$f" .json)" \
        'select(.state == "OPEN" and .author.login == $a) | {number: $n}' < "$f"
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
    printf '{"data":{"repository":{"pullRequest":%s}}}' "$(cat "$f")" | jq -r "$jq_expr"
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
        author="operator",
        verdict=None,
        threads=(),
        reviews=(),
    ) -> None:
        directory = self.fixtures / repo
        directory.mkdir(parents=True, exist_ok=True)
        node = {
            "state": state,
            "isDraft": draft,
            "reviewDecision": decision,
            "headRefOid": head,
            "headRefName": ref,
            "author": {"login": author},
            "reviews": {"nodes": [verdict] if verdict else []},
            "reviewThreads": {"nodes": list(threads)},
        }
        (directory / f"{number}.json").write_text(json.dumps(node))
        (directory / f"{number}.reviews.json").write_text(json.dumps(list(reviews)))

    def track(self, *entries) -> None:
        self.tracked.write_text("".join(f"{entry}\n" for entry in entries))

    def run_watcher(self, script, *args, down_after=None):
        """Make one pass. The call log and the failure switch cover this pass only."""
        log = self.fixtures / "calls.log"
        if log.exists():
            log.unlink()
        env = dict(self.env)
        if down_after is not None:
            env["FAKE_GH_DOWN_AFTER"] = str(down_after)
        return subprocess.run(
            ["bash", str(script), "--tracked", str(self.tracked), "--state", str(self.state), "--once", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
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
        self.assertEqual([["acme/app", "1", SHA_A, "feature-a", SHA_A]], self.state_rows())

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
        self.pull_request(1, head=SHA_A, decision="CHANGES_REQUESTED", threads=[thread("T2")])
        reopened = self.run_watcher(PRS)
        after_reopened = self.state_rows()
        self.pull_request(1, head=SHA_A, decision="CHANGES_REQUESTED", threads=[thread("T2", resolved=True)])
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

        self.assertEqual(2, malformed.returncode)
        self.assertIn(f"{self.tracked}:2 is not owner/repo#number: acme/app 2", malformed.stderr)
        self.assertEqual(2, missing.returncode)
        self.assertIn("acme/app#7: no such repository or pull request", missing.stderr)
        self.assertEqual(2, misspelled.returncode)
        self.assertIn("acme/ap#1: no such repository or pull request", misspelled.stderr)
        self.assertNotIn("could not verify", misspelled.stderr)
        self.assertEqual(2, unreachable.returncode)
        self.assertIn("could not verify — network or auth error", unreachable.stderr)
        self.assertEqual(2, author.returncode)
        self.assertIn("--repo and --author go together", author.stderr)

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
