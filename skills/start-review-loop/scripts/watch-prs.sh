#!/usr/bin/env bash
# watch-prs.sh — emit one line per reviewable event on the pull requests a
# review loop tracks, and stay silent otherwise. Intended to run under Claude
# Code's `Monitor`, where each stdout line becomes one notification.
#
#   NEW PR     a tracked pull request the state file has not seen, unless its
#              head already has a verdict by the --model model that the
#              --reviewer account posted
#   NEW HEAD   a tracked pull request's head SHA moved
#   RESPONDED  the author answered without pushing on a pull request still
#              waiting for a review: a draft by the --author login, or any
#              draft without one (self-review, which signs off by draft
#              state), or one whose newest Projector verdict on its head
#              requests changes (cross-author, where draft state is never
#              touched). Answered means every thread is
#              resolved, or the newest comment on an open Projector finding
#              thread is the author's rather than the reviewer's. No head
#              event is coming, so this asks for a re-review of the same
#              head. Once per answer, and never alongside NEW PR or NEW HEAD,
#              which already say to review that head. A hold the author lifts
#              is an answer too, and while a hold is open nothing is: the
#              author is building changes, so the head waits on them.
#   CLOSED     a tracked pull request is no longer open
#   BRANCH     the watched worktree changed branch
#   TRACKED    a problem with the tracked file: a line that is not
#              owner/repo#number, or a number that names no pull request.
#              Announced once each, so a mistake in the file is heard rather
#              than mistaken for a quiet repository
#
# Usage:
#   watch-prs.sh --tracked <file> --state <path> [--model <id> --reviewer <login>]
#                [--repo <owner/repo> [--author <login>] [--review-requested <login>]]
#                [--interval 60] [--worktree <dir>] [--once]
#
# --tracked names the file holding the tracked set: one `owner/repo#number`
# per line, with blank lines and lines starting with `#` ignored. The file is
# the whole scope of the watch, so a push to a pull request absent from it
# produces no event, and every event that does arrive is the loop's to review.
# That matters most here, because this watcher's events are pushes: on a
# shared repository every push by anyone is one, and every line is a Monitor
# notification counting toward the limit that stops a watcher. The file is
# re-read every cycle: the loop adds a pull request with `project loop add`
# and removes a merged one with `project loop remove`, without a restart. Every
# line is checked before the watch starts, because a bad one would otherwise
# be a watch that starts cleanly and never mentions the pull request it was
# meant for; a bad line that appears later is announced once.
#
# --repo with --author adopts that author's pull requests: each cycle lists
# the author's open pull requests in the repository and appends every one the
# file does not name yet, which then reports as NEW PR. --repo with
# --review-requested adopts the open pull requests that request that login's
# review, the same way, whoever made the request: the author, a CODEOWNERS
# rule, or a bot. Only a request to the login itself counts, not one to a team
# it belongs to, since a team that CODEOWNERS requests can be asked on nearly
# every pull request in a repository. GitHub drops a request once that login
# reviews, so a pull request is adopted while its request stands and stays
# tracked after. Give either flag or both. Adoption only ever adds lines, so
# the file stays the scope. A line of the form `!owner/repo#number` keeps
# that pull request out of both the watch and adoption. A failed listing
# adopts nothing that cycle and is tried again.
#
# The state file is the watcher's memory of what it has seen, and only the
# watcher writes it. A tracked pull request with no row there reports as NEW
# PR, so a new or empty file announces every tracked pull request. --model
# and --reviewer name the reviewer the review loop runs as: a model ID within
# a GitHub account. With them, a pull request with no row whose head already
# carries a Projector verdict that account posted with that model gets its
# row without a word instead, since the head has had its exact-head review.
# A verdict by another account or another model never does, even one that
# counts for RESPONDED: a teammate whose loop runs the same model is another
# reviewer, not this one. The model is compared as `project review` compares
# one, lowercased and without a trailing bracketed variant such as `[1m]`, and
# the login without regard to case. A row for a pull request no longer in the
# tracked file is dropped without a word, since the loop removed it on
# purpose; CLOSED is for the other direction, a listed pull request that left
# the open set.
#
# --once makes a single pass and exits, which is how the loop checks, before
# arming the watch, that the file names the pull requests it meant to track.

set -uo pipefail

TRACKED=""; STATE=""; INTERVAL=60; WORKTREE=""; ONCE=0; REPO=""; AUTHOR=""; REQUESTED=""; MODEL=""; REVIEWER=""

# Given the remaining arguments, refuse a flag that has no value after it,
# where `shift 2` would fail without shifting and the loop would never end.
takes_value() { [ $# -ge 2 ] || { echo "watch-prs.sh: $1 needs a value" >&2; exit 2; }; }

while [ $# -gt 0 ]; do
  case "$1" in
    --tracked)  takes_value "$@"; TRACKED="$2"; shift 2 ;;
    --repo)     takes_value "$@"; REPO="$2"; shift 2 ;;
    --author)   takes_value "$@"; AUTHOR="$2"; shift 2 ;;
    --review-requested) takes_value "$@"; REQUESTED="$2"; shift 2 ;;
    --model)    takes_value "$@"; MODEL="$2"; shift 2 ;;
    --reviewer) takes_value "$@"; REVIEWER="$2"; shift 2 ;;
    --state)    takes_value "$@"; STATE="$2"; shift 2 ;;
    --interval) takes_value "$@"; INTERVAL="${2:-60}"; shift 2 ;;
    --worktree) takes_value "$@"; WORKTREE="$2"; shift 2 ;;
    --once)     ONCE=1; shift ;;
    *) echo "watch-prs.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$TRACKED" ] || { echo "watch-prs.sh: --tracked is required" >&2; exit 2; }
[ -n "$STATE" ] || { echo "watch-prs.sh: --state is required" >&2; exit 2; }
touch "$STATE" 2>/dev/null || { echo "watch-prs.sh: cannot write state file: $STATE" >&2; exit 2; }
if [ -n "$REPO$AUTHOR$REQUESTED" ]; then
  [ -n "$REPO" ] && [ -n "$AUTHOR$REQUESTED" ] || { echo "watch-prs.sh: --repo goes with --author, --review-requested, or both" >&2; exit 2; }
  [[ $REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "watch-prs.sh: --repo is not owner/repo: $REPO" >&2; exit 2; }
  # The login lands inside a search query, where a space would add a term.
  [ -z "$REQUESTED" ] || [[ $REQUESTED =~ ^[A-Za-z0-9-]+$ ]] || { echo "watch-prs.sh: --review-requested is not a GitHub login: $REQUESTED" >&2; exit 2; }
  # An adopting watch may start from nothing; adoption fills the file.
  touch "$TRACKED" 2>/dev/null || { echo "watch-prs.sh: cannot write tracked file: $TRACKED" >&2; exit 2; }
fi
if [ -n "$MODEL$REVIEWER" ]; then
  [ -n "$MODEL" ] && [ -n "$REVIEWER" ] || { echo "watch-prs.sh: --model goes with --reviewer" >&2; exit 2; }
  [[ $REVIEWER =~ ^[A-Za-z0-9-]+$ ]] || { echo "watch-prs.sh: --reviewer is not a GitHub login: $REVIEWER" >&2; exit 2; }
  # Verdicts are looked up as <login>/<model> in a comma-separated list, and
  # no model there holds whitespace or a comma, so an ID that does would
  # never match and every reviewed head would announce.
  [[ $MODEL =~ ^[A-Za-z0-9][^[:space:],]*$ ]] || { echo "watch-prs.sh: --model is not a model ID: $MODEL" >&2; exit 2; }
fi

# The skill's own floor: never poll GitHub harder than every 30 seconds.
[ "$INTERVAL" -lt 30 ] 2>/dev/null && INTERVAL=30

# Print the tracked set as one "owner/repo number" pair per line, and every
# problem with the file to stderr, one per line. Returns 1 when the file
# cannot be read and 2 when a line is malformed; the well-formed lines are
# still printed in that case, so a running watch can keep to them.
#
# A `!` line removes its pull request wherever it sits in the file, since
# adoption may already have appended the plain line above it. GitHub names
# ignore case, so exclusions and duplicates are matched case-insensitively
# and the first spelling of an entry is the one watched.
ENTRY_RE='^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$'
parse_tracked() {
  local line key lineno=0 rc=0 entries="" excluded=" " seen=" "
  [ -r "$TRACKED" ] || { echo "$TRACKED: cannot read the tracked file" >&2; return 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    case "$line" in ""|\#*) continue ;; esac
    if [ "${line:0:1}" = "!" ] && [[ ${line#!} =~ $ENTRY_RE ]]; then
      excluded="$excluded$(lower "${line#!}") "
    elif [[ $line =~ $ENTRY_RE ]]; then
      entries="$entries$line
"
    else
      echo "$TRACKED:$lineno is not owner/repo#number: $line" >&2
      rc=2
    fi
  done < "$TRACKED"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    key=$(lower "$line")
    case "$excluded$seen" in *" $key "*) continue ;; esac
    seen="$seen$key "
    printf '%s %s\n' "${line%#*}" "${line##*#}"
  done <<EOF
$entries
EOF
  return $rc
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

VARIANT_RE='^(.*)\[[^][]*\]$'
MODEL_ID=$(lower "$MODEL")
[[ $MODEL_ID =~ $VARIANT_RE ]] && MODEL_ID="${BASH_REMATCH[1]}"
REVIEWER_ID=$(lower "$REVIEWER")

# Whether $1, the comma-separated <login>/<model> pairs that gave a head a
# verdict, names the --reviewer account with the --model model.
already_reviewed() {
  [ -n "$MODEL_ID" ] || return 1
  case ",$1," in *",$REVIEWER_ID/$MODEL_ID,"*) return 0 ;; esac
  return 1
}

# One row per pull request: state, draft flag, verdict, head SHA, head ref,
# how many threads are unresolved, and the newest unanswered author reply on
# an open Projector finding thread ("-" when there is none): the id of a last
# comment that carries neither the finding marker nor the reviewer's verify
# marker, so the author spoke last. The draft flag and verdict are what
# RESPONDED needs to know a head is waiting, and both signals are needed
# because the two review modes record the wait in different places and
# neither covers the other. Draft state carries a self-review: GitHub allows
# only a COMMENT review on your own pull request. The verdict carries a
# cross-author review, where draft state is untouched — the skill forbids
# drafting another author's pull request.
#
# The verdict is the newest Projector verdict on the head, read from the
# marker line its review body carries, or "-" when the head has none. It is
# not reviewDecision, which GitHub sets only where the base branch requires
# reviews. A verdict on an earlier head does not count: after a push the head
# waits on its own review, even once that review resolves the threads it
# settles and before it publishes. A verdict counts as `project review status`
# counts another account's: in a public repository only from an owner, a
# member, or a collaborator, since anyone can review there. Each thread reply
# files a review too, but a head waiting on its author gathers few, so its
# verdict is among the newest 100.
#
# The next field is "held" while the pull request's author has a hold comment
# open on it, one whose body carries `<!-- projector-hold v=1 -->`, and "-"
# otherwise. Only the author's comment counts, because anyone can comment. A
# hold lives only while its changes are built, so it is among the newest 100
# comments. The field after it names who gave the head a verdict, each as
# <login>/<model>, lowercased, without a trailing bracketed variant, and
# separated by commas, or "-" when nobody did. Unlike the verdict, it takes a
# review whatever its author's association: only the pair that names the
# --reviewer account is used, and `project review status` counts that
# account's own reviews the same way. The last field is the author's login.
PR_QUERY='query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){
  visibility
  pullRequest(number:$n){
    state isDraft headRefOid headRefName author{login}
    comments(last:100){ nodes{ author{login} body } }
    reviews(last:100){ nodes{ author{login} authorAssociation submittedAt body } }
    reviewThreads(first:100){ nodes{ isResolved
      comments(first:1){ nodes{ databaseId body } }
      lastComment: comments(last:1){ nodes{ databaseId body } } } } } } }'
PR_JQ='(.data.repository.visibility | IN("PRIVATE", "INTERNAL")) as $closed
  | .data.repository.pullRequest
  | .headRefOid as $head
  | [.reviews.nodes[]
      | ($closed or (.authorAssociation | IN("OWNER", "MEMBER", "COLLABORATOR"))) as $counted
      | (.author.login // "") as $login
      | .submittedAt as $at
      | .body // ""
      | capture("(?m)^<!-- projector-review v=1 verdict=(?<verdict>[a-z-]+) (?:[^>]*?model=(?<model>\\S+) )?[^>]*?sha=(?<sha>[0-9a-f]{40}) ")
      | select(.sha == $head) | {at: $at, verdict, model, login: $login, counted: $counted}] as $verdicts
  | ([$verdicts[] | select(.counted)] | sort_by(.at) | last | .verdict // "-") as $verdict
  | ([$verdicts[] | select(.login != "" and (.model // "") != "") | "\(.login)/\(.model)"
      | ascii_downcase | sub("\\[[^\\[\\]]*\\]$"; "") | select(contains(",") | not)]
     | unique | join(",") | if . == "" then "-" else . end) as $reviewers
  | ([.reviewThreads.nodes[]
      | select(.isResolved == false)
      | select(.comments.nodes[0].body | test("projector-finding"))
      | ((.lastComment // .comments).nodes[-1])
      | select(.body | test("projector-(finding|verify)") | not)
      | .databaseId // 0] | max // "-") as $answer
  | (.author.login // "") as $author
  | (if $author != "" and any(.comments.nodes[];
         (.author.login // "") == $author and (.body // "" | contains("<!-- projector-hold v=1 -->")))
     then "held" else "-" end) as $held
  | "\(.state) \(.isDraft) \($verdict) \(.headRefOid) \(.headRefName) \([.reviewThreads.nodes[] | select(.isResolved == false)] | length) \($answer) \($held) \($reviewers) \($author)"'

# stdout: the row above for $1#$2. stderr is kept in $STATE.err so a failure
# can be told apart from a pull request that does not exist.
fetch_pr() {
  gh api graphql -f query="$PR_QUERY" -f o="${1%%/*}" -f r="${1##*/}" -F n="$2" \
    --jq "$PR_JQ" 2>"$STATE.err"
}

# The messages GraphQL returns for a number that names no pull request and
# for a slug that names no repository. Both mean the line is wrong as written,
# and a misspelled slug fails on every pass exactly as a bad number does, so
# it is refused and announced the same way. Any other failure — network,
# proxy, rate limit, token — is a blip to retry.
not_found() {
  case "$1" in
    *"Could not resolve to a PullRequest"*|*"Could not resolve to a Repository"*) return 0 ;;
  esac
  return 1
}

# Print the number of every open pull request in $REPO that $AUTHOR wrote or
# that requests $REQUESTED's review. A listing that fails prints nothing, so
# the other one still adopts this cycle.
listed() {
  [ -z "$AUTHOR" ] || gh pr list --repo "$REPO" --author "$AUTHOR" --state open --limit 200 \
    --json number --jq '.[].number' 2>"$STATE.err"
  [ -z "$REQUESTED" ] || gh pr list --repo "$REPO" --search "user-review-requested:$REQUESTED" \
    --state open --limit 200 --json number --jq '.[].number' 2>"$STATE.err"
}

# Append every pull request `listed` prints that the tracked file does not
# name yet, as an entry or as a `!` exclusion, in any letter case. It prints
# nothing: each appended line reports as NEW PR on this same pass.
adopt() {
  [ -n "$REPO" ] || return 0
  local n
  for n in $(listed); do
    awk -v a="$(lower "$REPO#$n")" \
      '{ gsub(/^[ \t]+|[ \t]+$/, ""); sub(/^!/, "") } tolower($0) == a { found = 1 } END { exit !found }' \
      "$TRACKED" 2>/dev/null && continue
    # Keep a last line without a newline from absorbing the appended one.
    [ -s "$TRACKED" ] && [ -n "$(tail -c 1 "$TRACKED")" ] && echo >> "$TRACKED"
    printf '%s#%s\n' "$REPO" "$n" >> "$TRACKED"
  done
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
# right all along. Either way nothing runs on an unverified set. Adopted lines
# come straight from GitHub's listing, so adoption waits for the first cycle.
entries=$(parse_tracked) || exit 2
while read -r slug n; do
  [ -n "${slug:-}" ] || continue
  if ! fetch_pr "$slug" "$n" >/dev/null; then
    err=$(cat "$STATE.err" 2>/dev/null)
    if not_found "$err"; then
      echo "watch-prs.sh: $slug#$n: no such repository or pull request" >&2
    else
      echo "watch-prs.sh: $slug#$n: could not verify — network or auth error, not a missing repository or pull request: ${err%%$'\n'*}" >&2
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
  adopt
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
    # Field 5 is the answer a RESPONDED line was last emitted for, as
    # <sha>:<reply id>, "held" while a hold is open, or "-". Comparing it
    # against the current head and newest reply is what makes that fire once
    # per answer: a re-review that requests changes again does not
    # re-announce, a further reply on the same head does, and a new push
    # resets it because the row's SHA changed. A bare SHA is the older form
    # of <sha>:-.
    flag=$(awk -v r="$slug" -v n="$n" '$1==r && $2==n {print $5}' "$STATE" 2>/dev/null)
    [ -n "$flag" ] || flag="-"

    # A failed query must never look like "closed". On any failure this pull
    # request's row is carried forward untouched and tried again next cycle;
    # otherwise a network blip would retire a live pull request and never
    # mention it again.
    if ! row=$(fetch_pr "$slug" "$n") || [ -z "$row" ]; then
      err=$(cat "$STATE.err" 2>/dev/null)
      if not_found "$err"; then
        announce_once "TRACKED $slug#$n: no such repository or pull request — ignored; remove it from the tracked file"
        continue
      fi
      [ -n "$known" ] && new_state="$new_state$slug $n $known $kref $flag
"
      continue
    fi
    read -r pstate pdraft pverdict sha ref unresolved answer held previewers pauthor <<EOF
$row
EOF
    [ -n "${answer:-}" ] || answer="-"
    key="$sha:$answer"
    [ "$flag" = "$sha" ] && flag="$sha:-"

    if [ "$pstate" != "OPEN" ]; then
      [ -n "$known" ] && echo "CLOSED $slug#$n (${kref:-$ref}) — no longer open; remove it from the tracked file"
      continue
    fi

    # A head this reviewer already reviewed is one this loop has seen. It still
    # goes through the RESPONDED test below, which hears an author who
    # answered that review while no watcher ran.
    [ -z "$known" ] && already_reviewed "$previewers" && known="$sha"

    if [ -z "$known" ]; then
      echo "NEW PR $slug#$n ($ref) head=${sha:0:7} — unreviewed, needs an exact-head review"
      # Announcing a head counts as having announced it, so the RESPONDED test
      # below suppresses a follow-up for it. Without this the head-unchanged
      # guard only defers the duplicate by one cycle: the row rebuild writes
      # this SHA into the state, so next cycle the guard passes and RESPONDED
      # fires about a head whose first review is probably still running.
      flag="$key"
    elif [ "$known" != "$sha" ]; then
      echo "NEW HEAD $slug#$n ($ref): ${known:0:7} -> ${sha:0:7} — needs an exact-head review"
      flag="$key"
    fi

    # Only when the head is known and unchanged — that is, when neither of
    # the two events above fired. RESPONDED exists for the case where nothing
    # was pushed: a draft, or a pull request whose verdict requests changes,
    # whose author resolved every thread or replied on an open finding is
    # waiting on *us*. The author answered, and a fix that produced no push —
    # a body correction, a reply, a declined finding — creates no new head to
    # notice. Nothing else will ever arrive, so watching heads alone
    # deadlocks. Which signal matched decides the wording, and they call for
    # opposite actions: a clean re-review marks a self-reviewed draft ready,
    # while on a cross-author changes request marking it ready is forbidden
    # and a clean re-review is a COMMENT recommending a human approval. So
    # with --author, only that author's draft waits on a review: a review
    # never moves another author's draft, which stays a draft after a clean
    # one. The draft is tested first because a self-review that requests
    # changes gives that verdict too, and drafts the pull request.
    #
    # An open hold says the author is building changes, so the head waits on
    # the author and nothing is a response; the flag records that a hold was
    # seen. Lifting it is the response: a hold lifted without a push, or one
    # that kept the new head's review in draft, leaves no other event to come.
    # A head event's pass records the hold too, because the push that carries
    # the changes can land a cycle before the hold is deleted.
    if [ "$held" = "held" ]; then
      flag="held"
    elif [ -n "$known" ] && [ "$known" = "$sha" ]; then
      rstate=""
      if [ "$unresolved" = "0" ] || [ "$answer" != "-" ]; then
        if [ "$pdraft" = "true" ] && { [ -z "$AUTHOR" ] || [ "$(lower "$pauthor")" = "$(lower "$AUTHOR")" ]; }; then
          rstate="draft"
        elif [ "$pverdict" = "changes-requested" ]; then
          rstate="changes-requested"
        fi
      fi
      if [ -n "$rstate" ]; then
        # Every thread resolved is one answer per head, so only the head is
        # compared: a head announced while a reply was outstanding, then
        # resolved by the watch loop or settled by that head's own review, is
        # the head already under review. A reply on an open finding is
        # compared by reply too, so each further reply asks again.
        if [ "$unresolved" = "0" ]; then
          seen="${flag%%:*}"; now="$sha"
          how="with every thread resolved"
        else
          seen="$flag"; now="$key"
          how="and the author replied on an open finding"
        fi
        [ "$flag" = "held" ] && how="now that its hold is lifted"
        if [ "$seen" != "$now" ]; then
          if [ "$rstate" = "changes-requested" ]; then
            echo "RESPONDED $slug#$n ($ref) head=${sha:0:7} — changes requested $how; re-review this head, and a clean one is a COMMENT, never an approval"
          else
            echo "RESPONDED $slug#$n ($ref) head=${sha:0:7} — still a draft $how; re-review this head and sign off if clean"
          fi
          flag="$key"
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
