#!/usr/bin/env bash
# watch-threads.sh — emit one line per piece of outstanding review work across
# every open pull request in one or more GitHub repositories. Silent when
# nothing is outstanding. Intended to run under Claude Code's `Monitor` with
# `persistent: true`, where each stdout line becomes one notification.
#
#   FINDING   an unresolved review thread that has not been announced recently
#   DRAFT     a pull request still in draft — no review loop has signed off on
#             its head. Not itself a finding: the work, if any, is in that pull
#             request's threads and review bodies, and a draft with none is
#             waiting on a re-review rather than on a change
#   REVIEW    a submitted review carrying a body — the shape every COMMENT
#             review posts, Projector's own included, which never moves
#             reviewDecision
#
# This watch is deliberately **level-triggered**: it reports what is currently
# unresolved rather than what just changed. An edge-triggered watch loses a
# finding permanently the one time it misses an edge — a thread posted while
# the watcher was starting, or during a network blip, is never "new" again and
# would go unmentioned forever. Re-announcing is the cheaper failure.
#
# Usage:
#   watch-threads.sh --repos owner/a[,owner/b...] --state <path>
#                    [--author login[,login...]] [--interval 60] [--renotify 900]
#
# --renotify is how many seconds before a still-unresolved thread is announced
# again. Set it high enough to be a reminder rather than a stream.
#
# --author narrows every event to pull requests those logins authored, which is
# how the fix loop keeps to the operator's own on a shared repository. Pass
# logins literally, never `@me`, whose value follows whichever token is live.
# Without the flag every open pull request is watched, which is the right
# default for a single-owner repository and the wrong one for a busy shared
# repo -- above all for DRAFT. The other three events are review activity:
# somebody had to open a thread or submit a review for them to fire, so they
# are bounded by work already aimed at a pull request. DRAFT fires on a pull
# request merely existing in draft, so unfiltered it announces every colleague's
# organic work-in-progress, and re-announces it every --renotify seconds for as
# long as it stays open. Every line is a Monitor notification counting toward
# the limit that stops a watcher, and a stopped watcher leaves the fix loop
# deaf while its skill reads silence as "nothing outstanding".

set -uo pipefail

REPOS=""; STATE=""; INTERVAL=60; RENOTIFY=900; AUTHORS=""

while [ $# -gt 0 ]; do
  case "$1" in
    --repos)    REPOS="${2:-}"; shift 2 ;;
    --state)    STATE="${2:-}"; shift 2 ;;
    --interval) INTERVAL="${2:-60}"; shift 2 ;;
    --renotify) RENOTIFY="${2:-900}"; shift 2 ;;
    --author)   AUTHORS="${2:-}"; shift 2 ;;
    *) echo "watch-threads.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$REPOS" ] || { echo "watch-threads.sh: --repos is required" >&2; exit 2; }
[ -n "$STATE" ] || { echo "watch-threads.sh: --state is required" >&2; exit 2; }
touch "$STATE" 2>/dev/null || { echo "watch-threads.sh: cannot write state file: $STATE" >&2; exit 2; }

# The skill's own floor: never poll GitHub harder than every 30 seconds.
[ "$INTERVAL" -lt 30 ] 2>/dev/null && INTERVAL=30

REPO_LIST=$(printf '%s' "$REPOS" | tr ',' ' ')
AUTHOR_LIST=$(printf '%s' "$AUTHORS" | tr ',' ' ')

# A login that does not exist is not an error to `gh pr list --author`: it is
# an empty answer, exit 0, on every pass, so a mistyped operator login gives a
# watch that starts cleanly and never says anything. Refuse it once, here.
# Distinguish a missing login from a failed lookup: `gh api` exits non-zero for
# a network failure, a rate limit or a bad token exactly as it does for a 404,
# and reporting "no such login" for a blip sends the reader off to re-resolve a
# login that was right all along. Either way nothing runs on an unverified one.
for a in $AUTHOR_LIST; do
  case "$a" in
    @*)    echo "watch-threads.sh: --author takes a literal login, not $a" >&2; exit 2 ;;
    app/*) probe="apps/${a#app/}" ;;   # `gh pr list --author app/dependabot`
    *)     probe="users/$a" ;;         # a user, or a bot as `dependabot[bot]`
  esac
  if ! err=$(gh api "$probe" 2>&1 >/dev/null); then
    case "$err" in
      *"HTTP 404"*) echo "watch-threads.sh: --author $a: no such GitHub login" >&2 ;;
      *) echo "watch-threads.sh: --author $a: could not verify login — network or auth error, not a missing login: ${err%%$'\n'*}" >&2 ;;
    esac
    exit 2
  fi
done

# True when no narrowing is in force, or when $1 is one of the watched logins.
# The verdict query below filters with this rather than in its jq, so it keeps
# paginating over every open pull request instead of inheriting the listing's
# 200 ceiling.
author_watched() {
  [ -n "$AUTHOR_LIST" ] || return 0
  local a
  for a in $AUTHOR_LIST; do
    [ "$a" = "$1" ] && return 0
  done
  return 1
}

# One pull request number per line for $1, restricted to $AUTHOR_LIST when it
# is set. Listed once per author rather than filtered client-side, so --limit
# bounds each author's pull requests rather than the repository's: on a repo
# with more than 200 open, a client-side filter over the first 200 would drop
# the operator's own past the cut. Any listing failing fails the whole call, so
# the caller carries this repository's rows forward rather than reading a
# partial answer as "everything else resolved".
list_prs() {
  local repo="$1" a chunk out=""
  if [ -z "$AUTHOR_LIST" ]; then
    gh pr list --repo "$repo" --state open --limit 200 \
      --json number --jq '.[].number' 2>/dev/null
    return
  fi
  for a in $AUTHOR_LIST; do
    chunk=$(gh pr list --repo "$repo" --author "$a" --state open --limit 200 \
              --json number --jq '.[].number' 2>/dev/null) || return 1
    [ -n "$chunk" ] && out="$out$chunk
"
  done
  printf '%s' "$out"
}

while true; do
  now=$(date +%s)
  new_state=""

  for slug in $REPO_LIST; do
    owner="${slug%%/*}"; name="${slug##*/}"

    # A failed query must not be read as "nothing outstanding". Carry this
    # repo's rows forward so a blip cannot silently retire a live finding.
    # --limit is not optional: `gh pr list` defaults to 30, and pull requests
    # past that page are never scanned, so their findings are never announced
    # at all. Scans threads for up to 200 open pull requests per repository;
    # the verdict query below paginates instead and has no such ceiling, so
    # the 200 is this listing's limit and not the script's.
    if ! prs=$(list_prs "$slug"); then
      carried=$(grep " $slug " "$STATE" 2>/dev/null || true)
      [ -n "$carried" ] && new_state="$new_state$carried
"
      continue
    fi

    # Outstanding work with no thread attached to it takes two shapes, and this
    # one query answers for both across the whole repository. A reviewer may put
    # every finding in the review summary body and open no inline thread at all,
    # and then the thread query below is correctly silent while the pull request
    # sits blocked. Watching threads alone reads that as "nothing outstanding",
    # which is the one reading that must never be wrong.
    #
    #   VERDICT — a real CHANGES_REQUESTED review. Available only when the
    #   reviewer is not the pull request's author: a human teammate, Codex,
    #   Bugbot, or a cross-author Projector review loop. This stays first-class;
    #   the fix loop's whole job is answering real reviewers.
    #
    #   DRAFT — a pull request the review loop has not signed off. On a
    #   self-review GitHub refuses APPROVE and REQUEST_CHANGES on your own pull
    #   request, so every such review is a COMMENT review and reviewDecision
    #   never leaves NONE. Draft state carries the outcome instead, and its
    #   clearing is the handshake that says the head was accepted. Watching
    #   reviewDecision alone would be silent on every self-reviewed pull
    #   request forever, indistinguishable from a clean repository.
    #
    # Ask `reviews(states:[CHANGES_REQUESTED])` rather than `latestReviews`.
    # `latestReviews` is the most recent review *per author* whatever its
    # state, and replying inside a thread files a COMMENTED review — so a
    # reviewer answering their own thread displaces their standing changes
    # request out of that connection while `reviewDecision` stays
    # CHANGES_REQUESTED. The pull request would pass the outer filter, match
    # nothing inside, and emit nothing: a false negative in exactly the case
    # this query exists to catch, and one that correlates with discussion, so
    # it goes quiet on the pull requests being argued about and stays loud on
    # the ones nobody has touched. `last:1` is one line per pull request
    # rather than per author, which is what the skill documents.
    #
    # Paginated, and it has to be: GraphQL connections cap `first` at 100 —
    # `first:200` is rejected outright as EXCESSIVE_PAGINATION — so a single
    # page would watch 100 pull requests while the thread query above covers
    # 200. That asymmetry fails in the worst available direction. A pull
    # request past the page still has its threads watched, so a review with
    # inline findings is still seen; the one lost is a review whose findings
    # live only in the summary body, which is precisely the case this query
    # exists to catch. `orderBy` is pinned so the traversal is stable across
    # pages rather than arbitrary.
    if ! verdicts=$(gh api graphql --paginate -f query='
      query($o:String!,$r:String!,$endCursor:String){ repository(owner:$o,name:$r){
        pullRequests(states:OPEN, first:100, after:$endCursor,
                     orderBy:{field:CREATED_AT, direction:ASC}){
          pageInfo{ hasNextPage endCursor }
          nodes{
            number isDraft reviewDecision headRefOid
            author{login}
            reviews(states:[CHANGES_REQUESTED], last:1){ nodes{
              author{login} body commit{oid} } } } } } }' \
      -f o="$owner" -f r="$name" \
      --jq '.data.repository.pullRequests.nodes[] | . as $pr
            | ($pr.author.login // "") as $by
            | (if $pr.reviewDecision == "CHANGES_REQUESTED"
               then $pr.reviews.nodes[]
                    | "V\t\($pr.number)\t\(.commit.oid)\t\($by)\t\(.author.login // "?")\t\(.body // "" | gsub("[\r\n\t]+"; " ") | .[0:130])"
               else empty end),
              (if $pr.isDraft then "D\t\($pr.number)\t\($pr.headRefOid)\t\($by)\t\t" else empty end)' \
      2>/dev/null); then
      carried=$(awk -v s="$slug" '$1 ~ /^(verdict|draft):/ && $2==s' "$STATE" 2>/dev/null || true)
      [ -n "$carried" ] && new_state="$new_state$carried
"
    else
      # Field order matters: tab is an IFS whitespace character, so a run of
      # tabs collapses to one delimiter and an empty field in the middle of a
      # row silently shifts every field after it. The author is therefore
      # placed before the fields that can be empty, and the free-text snippet
      # stays last because `read` gives the final variable everything left --
      # a tab inside a review body would otherwise fake a field break.
      while IFS=$'\t' read -r kind vn vsha vby vwho vsnip; do
        [ -n "${vn:-}" ] || continue
        author_watched "${vby:-}" || continue
        # Keyed on the reviewed or head SHA, so a push is a new row and
        # announces immediately rather than waiting out --renotify.
        if [ "$kind" = V ]; then
          vid="verdict:$vwho:$vsha"
          first="VERDICT $slug#$vn CHANGES_REQUESTED on ${vsha:0:8} [$vwho] — $vsnip"
          again="VERDICT (still open) $slug#$vn CHANGES_REQUESTED on ${vsha:0:8} [$vwho] — $vsnip"
        else
          vid="draft:$vn:$vsha"
          first="DRAFT $slug#$vn head=${vsha:0:8} — no review loop has signed off this head; the work, if any, is in its threads and review bodies"
          again="DRAFT (still open) $slug#$vn head=${vsha:0:8} — still not signed off"
        fi
        last=$(awk -v t="$vid" '$1==t {print $4}' "$STATE" 2>/dev/null)
        if [ -z "$last" ]; then
          echo "$first"
          last=$now
        elif [ $((now - last)) -ge "$RENOTIFY" ]; then
          echo "$again"
          last=$now
        fi
        new_state="$new_state$vid $slug $vn $last
"
      done <<EOF
$verdicts
EOF
    fi

    for n in $prs; do
      if ! threads=$(gh api graphql -f query='
        query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){
          pullRequest(number:$n){ reviewThreads(first:100){ nodes{
            id isResolved path line
            comments(last:1){nodes{author{login} body}} } } } } }' \
        -f o="$owner" -f r="$name" -F n="$n" \
        --jq '.data.repository.pullRequest.reviewThreads.nodes[]
              | select(.isResolved == false)
              | "\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login // "?")\t\(.comments.nodes[0].body // "" | gsub("<!--([^-]|-[^-]|--[^>])*-->"; "") | gsub("[\r\n\t]+"; " ") | sub("^ +"; "") | .[0:130])"' \
        2>/dev/null); then
        # The same rule as the repo-level guard, for the same reason, and it is
        # the case that falls between them: the repo query answered but this
        # one did not. Dropping this pull request's rows would blank every one
        # of its threads' last-announced stamps, so next cycle they all
        # re-announce as fresh findings at --interval rather than --renotify.
        # That is a flood, on a repository whose API is already flaky, and
        # Monitor stops a watcher that produces too many events — leaving the
        # fix loop deaf while its skill reads silence as "nothing outstanding".
        # Thread and review rows, excluding verdict and draft rows. The test is
        # which kinds have already been rebuilt at this point: both of those
        # were, above, per repository, so carrying them again here would
        # duplicate them. Review rows are rebuilt below — past the `continue`
        # on the next line — so excluding them here drops them outright,
        # neither carried nor rebuilt, and every review on this pull request
        # re-announces next cycle with no --renotify damping to throttle it.
        #
        # This is the third row kind added to this file, and the third time
        # this carry-forward had to learn about one. The general rule: carry
        # every kind whose rebuild happens later in this iteration, exclude
        # only those already rebuilt before it.
        carried=$(awk -v s="$slug" -v p="$n" '$1 !~ /^(verdict|draft):/ && $2==s && $3==p' "$STATE" 2>/dev/null || true)
        [ -n "$carried" ] && new_state="$new_state$carried
"
        continue
      fi

      # Strip HTML comments before snipping, the way the review query below
      # does. Findings open with `<!-- projector-finding v=1 priority=<P>
      # sha=<40-hex> -->` and fix-loop replies with `<!-- projector-reply
      # v=1 -->`; the first is about 88 characters, so against a 130-character
      # snippet it would leave roughly 42 characters of the actual finding in
      # every notification the fix loop sees. Unlike the review query this one
      # does not drop a body that strips to nothing: a review with no body is
      # only a reply-carrier, but an unresolved thread is outstanding work
      # whatever its last comment looks like, and dropping it is the one
      # reading that must never be wrong. Tags are deliberately left alone --
      # `<operator>` and friends are content here, not markup.
      while IFS=$'\t' read -r tid loc who snip; do
        [ -n "${tid:-}" ] || continue
        last=$(awk -v t="$tid" '$1==t {print $4}' "$STATE" 2>/dev/null)
        if [ -z "$last" ]; then
          echo "FINDING $slug#$n $loc [$who] — $snip"
          last=$now
        elif [ $((now - last)) -ge "$RENOTIFY" ]; then
          echo "FINDING (still open) $slug#$n $loc [$who] — $snip"
          last=$now
        fi
        new_state="$new_state$tid $slug $n $last
"
      done <<EOF
$threads
EOF

      # A review can carry findings and still never be a verdict. Codex and
      # Bugbot submit COMMENTED reviews, so reviewDecision never moves and the
      # verdict query is blind to them; their findings are seen only when they
      # arrive as inline threads, and a body-only review — a failed anchor, a
      # summary with no thread under it — would otherwise vanish without a
      # trace. REST rather than GraphQL, and per pull request rather than per
      # repository, because a reviews connection can only be windowed and the
      # window is exactly the trap: every reply the fix loop posts inside a
      # thread files an empty COMMENTED review, so `last:10` returns ten
      # replies and the bot review has been displaced — the latestReviews
      # failure again, one query over. One line per review, keyed on the
      # review's own id. Never re-announced: a COMMENTED
      # review has no resolved state to clear, so "(still open)" would be a
      # reminder with no off switch. Bodies empty once HTML comments are
      # stripped are skipped, which is also what keeps the loop's own replies
      # from coming back to it as work.
      if ! reviews=$(gh api "repos/$slug/pulls/$n/reviews" --paginate \
        --jq '.[] | select(.state == "COMMENTED")
              | (.body // "" | gsub("<!--([^-]|-[^-]|--[^>])*-->"; "") | gsub("<[^>]*>"; "") | gsub("[\r\n]+"; " ")) as $body
              | select(($body | gsub("[[:space:]]+"; "")) != "")
              | "\(.id)\t\(.user.login)\t\(.commit_id)\t\($body[0:130])"' \
        2>/dev/null); then
        carried=$(awk -v s="$slug" -v p="$n" '$1 ~ /^review:/ && $2==s && $3==p' "$STATE" 2>/dev/null || true)
        [ -n "$carried" ] && new_state="$new_state$carried
"
      else
        while IFS=$'\t' read -r crid cwho csha csnip; do
          [ -n "${crid:-}" ] || continue
          # Keyed on the review's own id, not author and SHA. The same account
          # can submit several COMMENTED reviews against one commit — Codex or
          # Bugbot re-run without a new push — and an author+SHA key collapses
          # them into one row: the second review finds the first's timestamp
          # and announces nothing, so its body-only findings are lost. The id
          # is unique per review, which is the thing being announced.
          cid="review:$crid"
          last=$(awk -v t="$cid" '$1==t {print $4}' "$STATE" 2>/dev/null)
          if [ -z "$last" ]; then
            echo "REVIEW $slug#$n by [$cwho] on ${csha:0:8} — $csnip"
            last=$now
          fi
          new_state="$new_state$cid $slug $n $last
"
        done <<REVEOF
$reviews
REVEOF
      fi
    done
  done

  # Rows are `<threadId> <owner/repo> <pr> <lastAnnouncedEpoch>`. Rebuilt each
  # cycle from what is currently unresolved, so a thread that got resolved
  # simply falls out. Rows are only ever carried forward for a query that
  # failed — the repository listing, or one pull request's threads — never for
  # one that answered.
  printf '%s' "$new_state" | grep -v '^[[:space:]]*$' > "$STATE.tmp" 2>/dev/null
  mv "$STATE.tmp" "$STATE" 2>/dev/null

  sleep "$INTERVAL"
done
