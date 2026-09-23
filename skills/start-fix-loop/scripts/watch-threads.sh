#!/usr/bin/env bash
# watch-threads.sh — emit one line per piece of outstanding review work on the
# pull requests a fix loop tracks, and stay silent otherwise. Intended to run
# under Claude Code's `Monitor`, where each stdout line becomes one
# notification.
#
#   FINDING   an unresolved review thread that has not been announced recently
#   VERDICT   a real CHANGES_REQUESTED review, including one whose findings
#             live only in its body
#   DRAFT     a pull request still in draft — no review loop has signed off on
#             its head. Not itself a finding: the work, if any, is in that pull
#             request's threads and review bodies, and a draft with none is
#             waiting on a re-review rather than on a change
#   REVIEW    a submitted review carrying a body — the shape every COMMENT
#             review posts, Projector's own included, which never moves
#             reviewDecision
#   TRACKED   a problem with the tracked file: a line that is not
#             owner/repo#number, or a number that names no pull request.
#             Announced once each, so a mistake in the file is heard rather
#             than mistaken for a quiet repository
#
# This watch is deliberately **level-triggered**: it reports what is currently
# unresolved rather than what just changed. An edge-triggered watch loses a
# finding permanently the one time it misses an edge — a thread posted while
# the watcher was starting, or during a network blip, is never "new" again and
# would go unmentioned forever. Re-announcing is the cheaper failure.
#
# Usage:
#   watch-threads.sh --tracked <file> --state <path>
#                    [--interval 60] [--renotify 900] [--once]
#
# --tracked names the file holding the tracked set: one `owner/repo#number`
# per line, with blank lines and lines starting with `#` ignored. The file is
# the whole scope of the watch. Nothing is listed and nothing is discovered, so
# a pull request absent from it produces no event however loud its threads
# are, and every event that does arrive is the loop's to act on. The file is
# re-read every cycle: the loop adds a pull request it opened or adopted by
# appending a line and drops a merged one by deleting its line, without a
# restart. Every line is checked before the watch starts, because a bad one
# would otherwise be a watch that starts cleanly and never mentions the pull
# request it was meant for; a bad line that appears later is announced once.
#
# --renotify is how many seconds before a still-unresolved thread is announced
# again. Set it high enough to be a reminder rather than a stream. Every line
# is a Monitor notification counting toward the limit that stops a watcher,
# and a stopped watcher leaves the fix loop deaf while its skill reads silence
# as "nothing outstanding".
#
# --once makes a single pass and exits, which is how the loop checks, before
# arming the watch, that the file names the pull requests it meant to track.

set -uo pipefail

TRACKED=""; STATE=""; INTERVAL=60; RENOTIFY=900; ONCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --tracked)  TRACKED="${2:-}"; shift 2 ;;
    --state)    STATE="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-60}"; shift 2 ;;
    --renotify) RENOTIFY="${2:-900}"; shift 2 ;;
    --once)     ONCE=1; shift ;;
    *) echo "watch-threads.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$TRACKED" ] || { echo "watch-threads.sh: --tracked is required" >&2; exit 2; }
[ -n "$STATE" ] || { echo "watch-threads.sh: --state is required" >&2; exit 2; }
touch "$STATE" 2>/dev/null || { echo "watch-threads.sh: cannot write state file: $STATE" >&2; exit 2; }

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

# Everything the watch needs to know about one pull request, in one query.
# The first row is the pull request itself; a V row is its standing
# CHANGES_REQUESTED review, if any; each T row is an unresolved thread.
#
# Ask `reviews(states:[CHANGES_REQUESTED])` rather than `latestReviews`.
# `latestReviews` is the most recent review *per author* whatever its state,
# and replying inside a thread files a COMMENTED review — so a reviewer
# answering their own thread displaces their standing changes request out of
# that connection while `reviewDecision` stays CHANGES_REQUESTED. `last:1` is
# one line per pull request, which is what the skill documents.
#
# Field order matters in every row: tab is an IFS whitespace character, so a
# run of tabs collapses to one delimiter and an empty field in the middle of a
# row silently shifts every field after it. Every field that can be null gets
# a default, and the free-text snippet stays last because `read` gives the
# final variable everything left — a tab inside a review body would otherwise
# fake a field break.
#
# Findings open with `<!-- projector-finding v=1 priority=<P> sha=<40-hex> -->`
# and fix-loop replies with `<!-- projector-reply v=1 -->`; the first is about
# 88 characters, so against a 130-character snippet it would leave roughly 42
# characters of the actual finding in every notification the fix loop sees.
# HTML comments are stripped before snipping for that reason. A thread whose
# last comment strips to nothing is still reported: an unresolved thread is
# outstanding work whatever its last comment looks like, and dropping it is
# the one reading that must never be wrong.
PR_QUERY='query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){
  pullRequest(number:$n){
    state isDraft reviewDecision headRefOid
    reviews(states:[CHANGES_REQUESTED], last:1){ nodes{ author{login} body commit{oid} } }
    reviewThreads(first:100){ nodes{
      id isResolved path line
      comments(last:1){nodes{author{login} body}} } } } } }'
PR_JQ='.data.repository.pullRequest as $pr
  | "P\t\($pr.state)\t\($pr.isDraft)\t\($pr.reviewDecision // "NONE")\t\($pr.headRefOid)",
    (if $pr.reviewDecision == "CHANGES_REQUESTED"
     then $pr.reviews.nodes[]
          | "V\t\(.commit.oid // "?")\t\(.author.login // "?")\t\(.body // "" | gsub("[\r\n\t]+"; " ") | .[0:130])"
     else empty end),
    ($pr.reviewThreads.nodes[]
     | select(.isResolved == false)
     | "T\t\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login // "?")\t\(.comments.nodes[0].body // "" | gsub("<!--([^-]|-[^-]|--[^>])*-->"; "") | gsub("[\r\n\t]+"; " ") | sub("^ +"; "") | .[0:130])")'

# stdout: the rows above for $1#$2. stderr is kept in $STATE.err so a failure
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

# A review can carry findings and never be a verdict. Codex and Bugbot submit
# COMMENTED reviews, so reviewDecision never moves and the verdict row is
# blind to them; their findings are seen only when they arrive as inline
# threads, and a body-only review — a failed anchor, a summary with no thread
# under it — would otherwise vanish without a trace. REST rather than GraphQL,
# because a GraphQL reviews connection can only be windowed and the window is
# exactly the trap: every reply the fix loop posts inside a thread files an
# empty COMMENTED review, so `last:10` returns ten replies and the bot review
# has been displaced. Bodies empty once HTML comments are stripped are
# skipped, which is also what keeps the loop's own replies from coming back
# to it as work.
REVIEWS_JQ='.[] | select(.state == "COMMENTED")
  | (.body // "" | gsub("<!--([^-]|-[^-]|--[^>])*-->"; "") | gsub("<[^>]*>"; "") | gsub("[\r\n]+"; " ")) as $body
  | select(($body | gsub("[[:space:]]+"; "")) != "")
  | "\(.id)\t\(.user.login // "?")\t\(.commit_id // "?")\t\($body[0:130])"'
fetch_reviews() {
  gh api "repos/$1/pulls/$2/reviews" --paginate --jq "$REVIEWS_JQ" 2>/dev/null
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
      echo "watch-threads.sh: $slug#$n: no such pull request" >&2
    else
      echo "watch-threads.sh: $slug#$n: could not verify — network or auth error, not a missing pull request: ${err%%$'\n'*}" >&2
    fi
    exit 2
  fi
done <<EOF
$entries
EOF

while true; do
  now=$(date +%s)
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

    # Fetch everything first and announce nothing until both answers are in.
    # A failed query must not be read as "nothing outstanding": on any
    # failure this pull request's rows are carried forward untouched so a
    # blip cannot silently retire a live finding, and so that next cycle its
    # threads do not all re-announce as fresh findings at --interval rather
    # than at --renotify. Carrying the whole pull request is one rule for
    # every kind of row, which is what keeps it right as kinds are added.
    if ! pr=$(fetch_pr "$slug" "$n") || [ -z "$pr" ]; then
      err=$(cat "$STATE.err" 2>/dev/null)
      if not_found "$err"; then
        announce_once "TRACKED $slug#$n: no such pull request — ignored; fix the tracked file"
        continue
      fi
      carried=$(awk -v s="$slug" -v p="$n" '$2==s && $3==p' "$STATE" 2>/dev/null || true)
      [ -n "$carried" ] && new_state="$new_state$carried
"
      continue
    fi
    if ! reviews=$(fetch_reviews "$slug" "$n"); then
      carried=$(awk -v s="$slug" -v p="$n" '$2==s && $3==p' "$STATE" 2>/dev/null || true)
      [ -n "$carried" ] && new_state="$new_state$carried
"
      continue
    fi

    IFS=$'\t' read -r kind pstate pdraft pdecision psha <<EOF
$(printf '%s\n' "$pr" | head -n 1)
EOF
    # Merged or closed: nothing here is outstanding, and its rows fall out.
    [ "$pstate" = "OPEN" ] || continue

    # DRAFT — a pull request the review loop has not signed off. On a
    # self-review GitHub refuses APPROVE and REQUEST_CHANGES on your own pull
    # request, so every such review is a COMMENT review and reviewDecision
    # never leaves NONE. Draft state carries the outcome instead, and its
    # clearing is the handshake that says the head was accepted. Keyed on the
    # head SHA, so a push is a new row and announces immediately rather than
    # waiting out --renotify.
    if [ "$pdraft" = "true" ]; then
      vid="draft:$n:$psha"
      last=$(awk -v t="$vid" '$1==t {print $4}' "$STATE" 2>/dev/null)
      if [ -z "$last" ]; then
        echo "DRAFT $slug#$n head=${psha:0:8} — no review loop has signed off this head; the work, if any, is in its threads and review bodies"
        last=$now
      elif [ $((now - last)) -ge "$RENOTIFY" ]; then
        echo "DRAFT (still open) $slug#$n head=${psha:0:8} — still not signed off"
        last=$now
      fi
      new_state="$new_state$vid $slug $n $last
"
    fi

    while IFS=$'\t' read -r kind f1 f2 f3 f4; do
      case "${kind:-}" in
        V)
          # VERDICT — a real CHANGES_REQUESTED review, available only when the
          # reviewer is not the pull request's author: a human teammate,
          # Codex, Bugbot, or a cross-author Projector review loop. Keyed on
          # the reviewed SHA, so a push is a new row.
          vid="verdict:$f2:$f1"
          last=$(awk -v t="$vid" '$1==t {print $4}' "$STATE" 2>/dev/null)
          if [ -z "$last" ]; then
            echo "VERDICT $slug#$n CHANGES_REQUESTED on ${f1:0:8} [$f2] — $f3"
            last=$now
          elif [ $((now - last)) -ge "$RENOTIFY" ]; then
            echo "VERDICT (still open) $slug#$n CHANGES_REQUESTED on ${f1:0:8} [$f2] — $f3"
            last=$now
          fi
          new_state="$new_state$vid $slug $n $last
"
          ;;
        T)
          last=$(awk -v t="$f1" '$1==t {print $4}' "$STATE" 2>/dev/null)
          if [ -z "$last" ]; then
            echo "FINDING $slug#$n $f2 [$f3] — $f4"
            last=$now
          elif [ $((now - last)) -ge "$RENOTIFY" ]; then
            echo "FINDING (still open) $slug#$n $f2 [$f3] — $f4"
            last=$now
          fi
          new_state="$new_state$f1 $slug $n $last
"
          ;;
      esac
    done <<EOF
$pr
EOF

    # Keyed on the review's own id, not author and SHA. The same account can
    # submit several COMMENTED reviews against one commit — Codex or Bugbot
    # re-run without a new push — and an author+SHA key collapses them into
    # one row: the second review finds the first's timestamp and announces
    # nothing, so its body-only findings are lost. Never re-announced: a
    # COMMENTED review has no resolved state to clear, so "(still open)" would
    # be a reminder with no off switch.
    while IFS=$'\t' read -r crid cwho csha csnip; do
      [ -n "${crid:-}" ] || continue
      cid="review:$crid"
      last=$(awk -v t="$cid" '$1==t {print $4}' "$STATE" 2>/dev/null)
      if [ -z "$last" ]; then
        echo "REVIEW $slug#$n by [$cwho] on ${csha:0:8} — $csnip"
        last=$now
      fi
      new_state="$new_state$cid $slug $n $last
"
    done <<EOF
$reviews
EOF
  done <<EOF
$entries
EOF

  # Rows are `<id> <owner/repo> <pr> <lastAnnouncedEpoch>`. Rebuilt each cycle
  # from what is currently outstanding on the tracked set, so a thread that
  # got resolved, a pull request that merged, or a line deleted from the
  # tracked file simply falls out. Rows are only ever carried forward for a
  # pull request whose query failed — never for one that answered.
  printf '%s' "$new_state" | grep -v '^[[:space:]]*$' > "$STATE.tmp" 2>/dev/null
  mv "$STATE.tmp" "$STATE" 2>/dev/null

  [ "$ONCE" = 1 ] && exit 0
  sleep "$INTERVAL"
done
