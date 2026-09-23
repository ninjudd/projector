#!/usr/bin/env bash
# watch-prs.sh — emit one line per reviewable event on the pull requests a
# review loop tracks, and stay silent otherwise. Intended to run under Claude
# Code's `Monitor`, where each stdout line becomes one notification.
#
#   NEW PR     a tracked pull request the state file has not seen
#   NEW HEAD   a tracked pull request's head SHA moved
#   RESPONDED  every thread resolved and the head unchanged on a pull request
#              still waiting for one: a draft (self-review, where the verdict
#              never moves) or one at CHANGES_REQUESTED (cross-author, where
#              draft state is never touched). The author answered without
#              pushing, so no head event is coming. Once per head, and never
#              alongside NEW PR or NEW HEAD, which already say to review that
#              head.
#   CLOSED     a tracked pull request is no longer open
#   BRANCH     the watched worktree changed branch
#   TRACKED    a problem with the tracked file: a line that is not
#              owner/repo#number, or a number that names no pull request.
#              Announced once each, so a mistake in the file is heard rather
#              than mistaken for a quiet repository
#
# Usage:
#   watch-prs.sh --tracked <file> --state <path>
#                [--interval 60] [--worktree <dir>] [--once]
#
# --tracked names the file holding the tracked set: one `owner/repo#number`
# per line, with blank lines and lines starting with `#` ignored. The file is
# the whole scope of the watch. Nothing is listed and nothing is discovered, so
# a push to a pull request absent from it produces no event, and every event
# that does arrive is the loop's to review. That matters most here, because
# this watcher's events are pushes: on a shared repository every push by
# anyone is one, and every line is a Monitor notification counting toward the
# limit that stops a watcher. The file is re-read every cycle: the loop adds a
# pull request it opened or adopted by appending a line and drops a merged one
# by deleting its line, without a restart. Every line is checked before the
# watch starts, because a bad one would otherwise be a watch that starts
# cleanly and never mentions the pull request it was meant for; a bad line
# that appears later is announced once.
#
# The state file is the loop's memory of what has been seen. Seed it with rows
# of `<owner/repo> <number> <sha> <ref>` to baseline heads as already reviewed;
# an empty file means every tracked pull request reports as new, which is the
# right default when the loop is establishing itself. A row for a pull request
# no longer in the tracked file is dropped without a word, since the loop
# removed it on purpose; CLOSED is for the other direction, a listed pull
# request that left the open set.
#
# --once makes a single pass and exits, which is how the loop checks, before
# arming the watch, that the file names the pull requests it meant to track.

set -uo pipefail

TRACKED=""; STATE=""; INTERVAL=60; WORKTREE=""; ONCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tracked)  TRACKED="${2:-}"; shift 2 ;;
    --state)    STATE="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-60}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    --once)     ONCE=1; shift ;;
    *) echo "watch-prs.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$TRACKED" ] || { echo "watch-prs.sh: --tracked is required" >&2; exit 2; }
[ -n "$STATE" ] || { echo "watch-prs.sh: --state is required" >&2; exit 2; }
touch "$STATE" 2>/dev/null || { echo "watch-prs.sh: cannot write state file: $STATE" >&2; exit 2; }

# The skill's own floor: never poll GitHub harder than every 30 seconds.
[ "$INTERVAL" -lt 30 ] 2>/dev/null && INTERVAL=30

# Print the tracked set as one "owner/repo number" pair per line, and every
# problem with the file to stderr, one per line. Returns 1 when the file
# cannot be read and 2 when a line is malformed; the well-formed lines are
# still printed in that case, so a running watch can keep to them.
ENTRY_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$'
parse_tracked() {
  local line lineno=0 rc=0
  [ -r "$TRACKED" ] || { echo "$TRACKED: cannot read the tracked file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    case "$line" in ""|\#*) continue ;; esac
    if [[ $line =~ $ENTRY_RE ]]; then
      printf '%s %s\n' "${line%#*}" "${line##*#}"
    else
      echo "$TRACKED:$lineno is not owner/repo#number: $line" >&2
      rc=2
    fi
  done < "$TRACKED"
  return $rc
}

# One row per pull request: state, draft flag, review decision, head SHA,
# head ref, and how many threads are unresolved. The last three fields are
# what RESPONDED needs, and both signals are needed because the two review
# modes record the wait in different places and neither covers the other.
# Draft state carries a self-review: GitHub allows only a COMMENT review on
# your own pull request, so reviewDecision never leaves NONE. reviewDecision
# carries a cross-author review, where REQUEST_CHANGES works and draft state
# is untouched — the skill forbids drafting another author's pull request.
PR_QUERY='query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){
  pullRequest(number:$n){
    state isDraft reviewDecision headRefOid headRefName
    reviewThreads(first:100){ nodes{ isResolved } } } } }'
PR_JQ='.data.repository.pullRequest
  | "\(.state) \(.isDraft) \(.reviewDecision // "NONE") \(.headRefOid) \(.headRefName) \([.reviewThreads.nodes[] | select(.isResolved == false)] | length)"'

# stdout: the row above for $1#$2. stderr is kept in $STATE.err so a failure
# can be told apart from a pull request that does not exist.
fetch_pr() {
  gh api graphql -f query="$PR_QUERY" -f o="${1%%/*}" -f r="${1##*/}" -F n="$2" \
    --jq "$PR_JQ" 2>"$STATE.err"
}

# The message GraphQL returns for a number that names no pull request. Any
# other failure — network, proxy, rate limit, token — is a blip to retry.
not_found() {
  case "$1" in *"Could not resolve to a PullRequest"*) return 0 ;; esac
  return 1
}

# Every line here is a notification, and a problem with the tracked file
# persists until someone edits it, so each distinct message is said once.
WARNED=""
announce_once() {
  case "$WARNED" in *"$1"*) return ;; esac
  echo "$1"
  WARNED="$WARNED$1
"
}

# Refuse a file that cannot be watched as written. A malformed line, or a
# number that names no pull request, would otherwise give a watch that starts
# cleanly and never mentions the pull request it was meant for. Say which
# failure it was: `gh` exits non-zero for a network failure, a rate limit or a
# bad token exactly as it does for a missing pull request, and reporting "no
# such pull request" for a blip sends the reader off to fix a number that was
# right all along. Either way nothing runs on an unverified set.
entries=$(parse_tracked) || exit 2
while read -r slug n; do
  [ -n "${slug:-}" ] || continue
  if ! fetch_pr "$slug" "$n" >/dev/null; then
    err=$(cat "$STATE.err" 2>/dev/null)
    if not_found "$err"; then
      echo "watch-prs.sh: $slug#$n: no such pull request" >&2
    else
      echo "watch-prs.sh: $slug#$n: could not verify — network or auth error, not a missing pull request: ${err%%$'\n'*}" >&2
    fi
    exit 2
  fi
done <<EOF
$entries
EOF

prev_branch=""
[ -n "$WORKTREE" ] && prev_branch=$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

while true; do
  new_state=""

  # Re-read the tracked set. A malformed line is announced once and the
  # well-formed ones are watched; a file that cannot be read, or that has
  # nothing well-formed left in it, keeps the set from the previous cycle,
  # since a watch that goes blank because of a typo is the failure this file
  # exists to prevent.
  problems=$(parse_tracked 2>&1 >"$STATE.entries")
  if [ -n "$problems" ]; then
    while IFS= read -r problem; do
      [ -n "$problem" ] && announce_once "TRACKED $problem"
    done <<EOF
$problems
EOF
  fi
  if [ -s "$STATE.entries" ] || [ -z "$problems" ]; then
    entries=$(cat "$STATE.entries")
  fi

  while read -r slug n; do
    [ -n "${slug:-}" ] || continue

    known=$(awk -v r="$slug" -v n="$n" '$1==r && $2==n {print $3}' "$STATE" 2>/dev/null)
    kref=$(awk -v r="$slug" -v n="$n" '$1==r && $2==n {print $4}' "$STATE" 2>/dev/null)
    # Field 5 is the SHA a RESPONDED line was last emitted for, or "-".
    # Comparing it against the current head is what makes that fire once per
    # head: a re-review that requests changes again does not re-announce, and
    # a new push resets it because the row's SHA changed.
    flag=$(awk -v r="$slug" -v n="$n" '$1==r && $2==n {print $5}' "$STATE" 2>/dev/null)
    [ -n "$flag" ] || flag="-"

    # A failed query must never look like "closed". On any failure this pull
    # request's row is carried forward untouched and tried again next cycle;
    # otherwise a network blip would retire a live pull request and never
    # mention it again.
    if ! row=$(fetch_pr "$slug" "$n") || [ -z "$row" ]; then
      err=$(cat "$STATE.err" 2>/dev/null)
      if not_found "$err"; then
        announce_once "TRACKED $slug#$n: no such pull request — ignored; fix the tracked file"
        continue
      fi
      [ -n "$known" ] && new_state="$new_state$slug $n $known $kref $flag
"
      continue
    fi
    read -r pstate pdraft pdecision sha ref unresolved <<EOF
$row
EOF

    if [ "$pstate" != "OPEN" ]; then
      [ -n "$known" ] && echo "CLOSED $slug#$n (${kref:-$ref}) — no longer open; drop it from the tracked file"
      continue
    fi

    if [ -z "$known" ]; then
      echo "NEW PR $slug#$n ($ref) head=${sha:0:7} — unreviewed, needs an exact-head review"
      # Announcing a head counts as having announced it, so the RESPONDED test
      # below suppresses a follow-up for it. Without this the head-unchanged
      # guard only defers the duplicate by one cycle: the row rebuild writes
      # this SHA into the state, so next cycle the guard passes and RESPONDED
      # fires about a head whose first review is probably still running.
      flag="$sha"
    elif [ "$known" != "$sha" ]; then
      echo "NEW HEAD $slug#$n ($ref): ${known:0:7} -> ${sha:0:7} — needs an exact-head review"
      flag="$sha"
    fi

    # Only when the head is known and unchanged — that is, when neither of
    # the two events above fired. RESPONDED exists for the case where nothing
    # was pushed: a draft, or a pull request at CHANGES_REQUESTED, with every
    # thread resolved is waiting on *us*. The author answered, and a fix that
    # produced no push — a body correction, a reply, a declined finding —
    # creates no new head to notice. Nothing else will ever arrive, so
    # watching heads alone deadlocks. Which signal matched decides the
    # wording, and they call for opposite actions: a clean re-review marks a
    # self-reviewed draft ready, while on a cross-author changes request
    # marking it ready is forbidden and a clean re-review is a COMMENT
    # recommending a human approval.
    if [ -n "$known" ] && [ "$known" = "$sha" ]; then
      rstate=""
      if [ "$unresolved" = "0" ]; then
        if [ "$pdecision" = "CHANGES_REQUESTED" ]; then
          rstate="changes-requested"
        elif [ "$pdraft" = "true" ]; then
          rstate="draft"
        fi
      fi
      if [ -n "$rstate" ]; then
        if [ "$flag" != "$sha" ]; then
          if [ "$rstate" = "changes-requested" ]; then
            echo "RESPONDED $slug#$n ($ref) head=${sha:0:7} — changes requested with every thread resolved; re-review this head, and a clean one is a COMMENT, never an approval"
          else
            echo "RESPONDED $slug#$n ($ref) head=${sha:0:7} — still a draft with every thread resolved; re-review this head and sign off if clean"
          fi
          flag="$sha"
        fi
      else
        flag="-"
      fi
    fi
    new_state="$new_state$slug $n $sha $ref $flag
"
  done <<EOF
$entries
EOF

  printf '%s' "$new_state" | grep -v '^[[:space:]]*$' > "$STATE.tmp" 2>/dev/null
  mv "$STATE.tmp" "$STATE" 2>/dev/null

  if [ -n "$WORKTREE" ]; then
    b=$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [ -n "$b" ] && [ "$b" != "$prev_branch" ]; then
      echo "BRANCH $WORKTREE: ${prev_branch:-?} -> $b — look for an open PR on it"
      prev_branch="$b"
    fi
  fi

  [ "$ONCE" = 1 ] && exit 0
  sleep "$INTERVAL"
done
