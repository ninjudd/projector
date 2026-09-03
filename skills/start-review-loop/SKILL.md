---
name: start-review-loop
description: Monitor chat-owned pull requests in the current GitHub repository, review each exact pushed SHA locally as the operator, and publish verified findings as labeled COMMENT reviews — keeping a pull request in draft until its head is clean. Use when the user asks to keep reviewing current and subsequent PR heads.
---

# Start Review Loop

Run a persistent exact-head review loop for the current repository. A review
starts only when an unreviewed pushed SHA exists and local inspection of that
SHA actually begins.

## Resolve identity and scope

The loop runs as one GitHub identity, the **reviewer**. By default that is
simply the authenticated user. Resolve an override from explicit user input,
then configuration:

```sh
project config get review.username
```

That reads the GitHub login in `.projector.toml`, itself layered from the
repository outward to `~/.projector.toml`, so one file can set a reviewer for
every repository under a directory.

`get` exits `1` when the key is unset and no `--default` is given, and a key
set to an empty string exits `0` with an empty value. Both mean no override:
the reviewer is the authenticated user.

A reviewer login sourced from anywhere but explicit user input or that key is
not evidence — not your own notes, not a prior session's summary, not
repository lore. Nor does a note recording a past user instruction carry that
authority forward: explicit user input means this session's user, now. A
recalled note asserting that someone once approved an arrangement is the most
persuasive form this mistake takes, because it looks like the sanctioned
source rather than a substitute for it.

The operator's version of this rule fails loudly, filtering the watch to an
account with no matching pull requests and going silent. This one fails
quietly in the opposite direction, posting review after review as the wrong
account, so the source matters more here rather than less.

Confirm whatever you resolve under the token actually used:

```sh
gh auth status
gh api user --jq .login
```

The **operator** is the other role these instructions name: the account whose
pull requests are watched and whose branches carry the work. By default it is
that same authenticated user, which is why neither role needs configuring. A
reviewer override is what separates them, and every `<operator>` below means
this login, never the reviewer's.

The operator is deliberately not a setting. It is whoever the token says you
are, and a configuration file naming someone else would filter the watch to
that account's pull requests, match none of yours, and go quiet -- silence
this skill cannot tell apart from a repository with nothing outstanding.

Watch only the repository containing the current working directory. Track pull
requests this conversation creates or explicitly adopts; authorship by the
operator is necessary for ordinary ownership but does not adopt every pull
request from another session. The watcher may observe the operator's broader
set, but filter every event through the literal tracked set. Hold validated
logins literally in every query; never use `@me`. Under the default the two
roles coincide and `@me` looks harmless, but its value follows whichever token
is live, so under a reviewer override it resolves to the reviewer — an account
that authors nothing here — and the watch goes silent from the first pass on,
indistinguishable from a repository with nothing open.

### Two review modes, chosen per pull request

Compare the reviewer against each pull request's author. Nothing configures
this; it follows from who wrote the code.

Single-identity self-review is this loop's intended shape, not a degraded one
to engineer around. Its constraints are the point rather than the cost: a loop
reviewing its own work cannot move `reviewDecision`, so it cannot mark its own
code approved, and its verdict stays a claim a reader weighs instead of a
state that clears a merge gate. A second account would unlock those states,
which is precisely why adopting one is not a loop's call to make.

**Self-review — the reviewer authored the pull request.** GitHub permits only a
`COMMENT` review on your own pull request, refusing both `APPROVE` and
`REQUEST_CHANGES`, so `reviewDecision` never moves. Two conventions of this
skill carry the outcome instead: the **verdict line** in every review body, and
**draft state**, which is the status a reader sees in the pull-request list.

**Cross-author review — someone else wrote it.** Review verdict states work
normally here. Post `REQUEST_CHANGES` when findings are open. When the head is
clean, post a `COMMENT` review saying so — **never `APPROVE`**. An approval is a
person vouching for code, and a loop must not vouch on the reviewer's behalf;
recommend it in the body and let a human click it.

That last rule is the one configurable piece of this skill:

```sh
project config get review.allow_approve
```

`true` permits a real `APPROVE` on a clean cross-author review. Anything else,
including the key being unset, leaves it off -- treat a missing key as `false`
rather than as a reason to ask. It never applies to a self-review, where GitHub
refuses the verdict anyway. Because the setting is layered, a repository can
grant it without granting it everywhere, and `~/.projector.toml` can grant it
everywhere you work; read it per repository rather than once per machine.

### Draft means changes are needed

On your own pull request draft state *is* the verdict: draft means changes
are needed, ready means the head is clean. **A review with findings marks it
draft; a clean review marks it ready.** Publish the review, then `gh pr ready
<number>` or `gh pr ready <number> --undo` to match the verdict you just
gave. Nothing else enters into it — not when this loop first saw the pull
request, not who last changed the state, not whether it was ever a draft.
Setting a state it already holds is a no-op, so there is no case to analyse.

Draft carries the outcome there because GitHub refuses `APPROVE` and
`REQUEST_CHANGES` on your own pull request, so `reviewDecision` never moves
and draft state is the only list-visible channel left. On someone else's
`reviewDecision` does move, so draft state is not needed as a channel and is
not yours to move: post `REQUEST_CHANGES` and leave their state alone.

So **open your own pull requests as drafts by default**, every layer of a
stack included. The draft says the head has not been signed off; the ready
transition is the sign-off a reader sees in the pull-request list.

## Establish the loop

1. Read repository instructions and applicable review or GitHub workflow
   skills.
2. Resolve the repository, checked-out branch, the exact chat-owned pull
   request set, the operator's open pull requests, and the total open count:

   ```sh
   gh pr list --author <operator> --state open --limit 200
   ```

3. Fetch each pull request's base SHA, head SHA, state, reviews, and
   thread-aware review threads. Say how many operator pull requests are tracked
   out of the repository total.
4. Determine the last reviewed SHA from durable GitHub state or the current
   conversation. Review any current head without a completed exact-head review
   immediately; do not baseline it away.
5. Start or reuse a persistent recurring goal that records the repository,
   operator, tracked pull requests, and every reviewed SHA paired with the id
   of the review this loop published for it.
6. Run the bundled `scripts/watch-prs.sh` with a durable state file,
   `--author <operator>`, `--worktree <repository>`, and an interval of at
   least 30 seconds. Use the host's persistent process or monitor facility.
   Seed only SHAs already reviewed.

The watcher prints `NEW PR`, `NEW HEAD`, `RESPONDED`, `CLOSED`, and
`BRANCH`. Treat `RESPONDED` as a re-review request for a still-draft head whose
threads are all resolved: the author answered without pushing, so no head event
is coming, and only a re-review can sign off and mark it ready. It prevents a
body-only response from deadlocking both loops. Keep the loop silent while no
event needs action.

If adopting an existing watcher, fetch unresolved state first, then verify its
state file contains every expected row. Restart it when the script changed
after the process started. Silence alone proves nothing.

## Review an exact head

For every new head:

1. Confirm the pull request remains open and record its full SHA.
2. Fetch that commit and create a scratch worktree at it. Never run a
   reproducer that writes inside the operator's checkout. Never discard,
   restore, or overwrite the operator's dirty or uncommitted work while
   preparing or cleaning up a review.
3. Confirm the scratch worktree's `HEAD` equals GitHub's recorded SHA.
4. Only then post a concise start comment naming the short SHA, carrying the
   same signature line as a review body.
5. Inspect both the new range and the pull-request-wide integration diff. Read
   the code and documentation the change depends on.
6. Run focused tests and reproductions proportional to risk. Verify every
   prospective finding against the exact code.
7. Re-fetch the head before publishing. If it moved, revalidate findings and
   review the replacement SHA separately.

Do not invoke a separate Codex, Cursor, Bugbot, or other reviewer unless the
user explicitly requests that service. This skill performs the local review.

## Label every review and finding

The operator authors the pull request and posts the review, so nothing about
the author line distinguishes a Projector review from a human comment. Two
markers carry that distinction. Both are required, and both are conventions of
this skill — nothing in GitHub enforces them.

**Every review body opens with a signature line and a marker:**

```
🔭 **Projector review** · model `<model-id>` · effort `<effort>` · **<VERDICT>**

<!-- projector-review v=1 verdict=<approved|changes-requested> model=<model-id> effort=<effort> sha=<full-sha> findings=<n> -->
```

State the model and effort **actually running this review** — the model
identifier the host reports for the running session and its reasoning-effort or
thinking level — never a default copied from this file. A review whose
signature misstates what produced it is worse than an unlabeled one: a reader
weighs a finding by what reviewed it.

The visible verdict word is `APPROVED` or `CHANGES REQUESTED`, matching the
marker's `verdict=`. `approved` means no finding thread on the pull request is
open — a thread outlives the head it was filed on, so findings from earlier
heads count until resolved; `changes-requested` means at least one is open.
Under the verdict line, print the census the verdict rests on — `6 finding
threads: 4 resolved, 2 open` — counting the threads this review opens among the
open, so a changes-requested review on a fresh head never prints `0 open` above
its own findings. `approved` requires that last number to be zero, and printing
it lets a reader see the verdict was earned.

**Every inline finding opens with a marker:**

```
<!-- projector-finding v=1 priority=<P1|P2|P3> sha=<full-sha> -->
```

Keep the visible finding text as it always was: priority, impact, and the
verified code path or reproduction.

### Outstanding findings

A finding is outstanding while its thread is unresolved, and a head is
**clean** only when no finding thread is outstanding and this review opens
none. This query is the thread half of that test, and it pages so that a pull
request with more than a hundred threads is still read to the end. Run it by
thread state, not by author, and match the marker to separate Projector
findings from ordinary review conversation:

```sh
gh api graphql --paginate -f query='
  query($o:String!,$r:String!,$n:Int!,$endCursor:String){ repository(owner:$o,name:$r){
    pullRequest(number:$n){ reviewThreads(first:100, after:$endCursor){
      pageInfo{hasNextPage endCursor}
      nodes{ isResolved isOutdated path line originalLine
        comments(first:1){nodes{body}} } } } } }' \
  -f o=<owner> -f r=<repo> -F n=<number> \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[]
        | select(.isResolved == false)
        | select(.comments.nodes[0].body | test("projector-finding"))
        | "\(.path):\(.line // .originalLine)\(if .isOutdated then " outdated" else "" end)"'
```

An `outdated` row is a thread whose line the fix moved: GitHub nulls `line`
and keeps `originalLine`. It is still outstanding — outdated says the code
changed, not that the finding was addressed — and only the author resolving
the thread closes it.

Both loops key on the marker rather than on a login. A reply the fix loop posts
inside a thread carries `<!-- projector-reply v=1 -->` so it is never read back
as a new finding.

## Publish one review

Findings always go out as inline threads in **one** review, each carrying the
finding marker, and every review body carries the signature and verdict line.
Do not drip-feed findings or use ordinary issue comments for them. What differs
between the modes is only the review state and what marks the outcome.

GitHub anchors an inline comment only to a file within the first 3,000 files
of the diff, taken in path order; a thread on any later file fails with `422
Path could not be resolved`, however correct the path and line. On a pull
request that large, probe each anchor before the real submission: create a
pending review holding the one comment, then delete it. When a file falls
outside the window, anchor the finding to an in-window file that exercises the
same code — the CI step, test, or README line that runs it — and name the real
`path:line` in the first sentence, so a reader lands on the code rather than on
the anchor.

Immediately before submitting, list every verdict the reviewer has already
posted on this exact SHA. The endpoint pages at 30 rows, oldest first, and
every fix-loop thread reply adds a review object of its own, so the newest
verdict is the first row a single page loses:

```sh
gh api --paginate repos/<owner>/<repo>/pulls/<number>/reviews \
  --jq '.[] | select(.user.login == "<reviewer>")
        | select(.body | test("projector-review .* sha=<full-sha>"))
        | "\(.id) \(.submitted_at)"'
```

Compare each id against the record. An id this loop recorded is its own,
including the second verdict a `RESPONDED` re-review legitimately publishes on
an unmoved head, so it never trips the check. An id absent from the record
means another loop under the same account is reviewing this pull request. Hold
the review and ask the user which loop continues, rather than either submitting
or standing down. Two verdicts from one account on one commit read as a single
thorough loop, so the collision goes unnoticed unless this check catches it.
Standing down silently is no better: a second reviewer is sometimes invited
deliberately, and a head both loops walk away from gets no verdict at all. The
tracked-set filter does not prevent this, because both loops can legitimately
own the pull request.

A head is *clean* only when the outstanding-findings query above returns
nothing **and this review posts no finding**. Run the query now, before
choosing a branch. Both tests are one-way: an open thread makes the head not
clean whatever inspection found, and a new finding makes it not clean whatever
the query returned. The first matters most on a fix-cycle head, which arrives
with its threads exactly as the author left them: the reviewer never resolves
threads — only the author does — so a head you inspected and found nothing
wrong in is still not clean while a finding thread is open. The watcher applies
the thread test from its side: `RESPONDED` fires only when every thread is
resolved. `NEW HEAD` cannot, because a push says nothing about threads, so on
every head the check is yours.

**Findings open, self-review:** submit a `COMMENT` review, verdict
`changes-requested`, then convert it to a draft with `gh pr ready <number>
--undo`.

**Findings open, cross-author:** submit a `REQUEST_CHANGES` review, verdict
`changes-requested`. Leave draft state alone.

**Clean head, self-review:** submit a `COMMENT` review naming the exact SHA and
what was checked, verdict `approved`, then mark it ready with `gh pr ready
<number>`. That transition is the sign-off a reader sees in the pull-request
list.

**Clean head, cross-author:** submit a `COMMENT` review naming the exact SHA and
what was checked, verdict `approved`. Say plainly in the body that the head
looks clean and that a human approval is what remains. Post a real `APPROVE`
only where configuration explicitly permits it.

Never downgrade a `REQUEST_CHANGES` you could post to a `COMMENT` to work
around a permission failure, and never read GitHub's refusal of a self-review
verdict as one.

Record a SHA as reviewed, paired with the published review's id, only after that
review is published and, on a clean self-review, the pull request is ready.
Verify the input as well as the outputs: before an `approved` verdict, the
outstanding-findings query returned nothing and the review carries no finding;
after publishing, re-read the review body and the pull request's `isDraft`.
Never resolve the author's findings, claim a newer SHA was reviewed, or merge.
Resolving is the author's act, which is why your verification alone never closes
a finding.

## Gate readiness claims in plans

A plan's `status: ready` or `status: in-progress` is an executable-readiness
claim. On a pull request whose substance is a plan, or one that changes a plan
from `draft` to either of those, every question introduced or changed by that
pull request must be answered or explicitly deferred to implementation. A
readiness flip owns every question still open at the flip, even if the
questions predate the diff.

An owned unresolved question is a blocking inline finding. A pre-existing
question on a plan already at `ready` or `in-progress` is noted in the review
body rather than a thread, because this change did not introduce the blocker.
A question explicitly answered by building the implementation does not block.

Plans at `draft` or `completed` make no executable-readiness claim, so their
open questions do not withhold approval. A plan's `priority` — `now`, `next`,
or `later` — schedules the work and never claims readiness, so never gate on
it. Name the status and questions in the review body so the exemption is
visible. Run `project list --json`, match the changed path to the longest
canonical project directory, and read its state with
`project show <name> --json`. Supplemental documents carry no independent
status.

## Continue after fixes

Every pushed SHA, including a fix-only SHA, starts a complete review cycle.
Green CI and resolved threads are evidence about state, not substitutes for
review. Drop a pull request when it closes or merges, keep watching later
operator PRs, and continue until the user stops the loop.
