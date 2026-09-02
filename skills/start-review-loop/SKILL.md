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
every repository under a directory. Confirm whatever you resolve under the token actually used:

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

Never convert another author's pull request to a draft. Draft gating below is a
self-review mechanism; on someone else's pull request, `REQUEST_CHANGES` is
already the list-visible signal.

### Which pull requests Projector gates

Gating applies to self-review only. A tracked pull request is
**Projector-gated** when the reviewer authored it and it was a draft the first
time this loop saw it. Opening your own pull request as a draft is how you opt
in: the loop then owns its readiness and marks it ready for review on a clean
head, exactly like a self-review before the work goes out for human eyes.

So **open your own pull requests as drafts by default**, every layer of a
stack included. Gating is decided at first sight, and nothing this loop does
later reverses that: once it has seen a pull request ready, it will not demote
it, and the work goes to human review ungated. Converting your own pull
request to a draft by hand before the loop first sees it is the same opt-in
taken late, and the person's own state change is always allowed. What is not
a default is leaving it ready — that is a deliberate choice to skip the gate.

A pull request already ready for review when first seen is **not** gated.
Review it and publish findings exactly the same way, but never convert it to a
draft — it was published deliberately, and demoting it would retract a pull
request from human view without being asked.

**Explicit user instruction re-gates a tracked pull request**, and it is the
way back in after a ready first sight. That is the same authority heading every
other resolution order in this skill, and it is the only thing that overrides
first sight: a person converting an already-seen pull request to a draft does
not re-gate it on its own, because the loop must not read someone's state
change as an instruction to take ownership of their readiness. Without this
route a hand-drafted pull request would strand — a draft no loop will ever mark
ready, which the fix loop's watcher goes on announcing as unsigned-off. Record
the change in durable state and gate it normally from there.

Record gated-ness in the loop's durable state when a pull request is adopted.
Set draft or ready **once per reviewed head**, and never re-fight a state a
person changed by hand; their last word stands until a new head arrives.

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
   operator, tracked pull requests, which of them are Projector-gated, and
   reviewed SHAs.
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
marker's `verdict=`. `approved` means every finding on this exact head is
resolved or absent; `changes-requested` means at least one is open.

**Every inline finding opens with a marker:**

```
<!-- projector-finding v=1 priority=<P1|P2|P3> sha=<full-sha> -->
```

Keep the visible finding text as it always was: priority, impact, and the
verified code path or reproduction.

### Reading unresolved findings back

A finding is outstanding when its thread is unresolved. Enumerate them by
thread state, not by author, and match the marker to separate Projector
findings from ordinary review conversation:

```sh
gh api graphql -f query='
  query($o:String!,$r:String!,$n:Int!){ repository(owner:$o,name:$r){
    pullRequest(number:$n){ reviewThreads(first:100){ nodes{
      isResolved path line comments(first:1){nodes{body}} } } } } }' \
  -f o=<owner> -f r=<repo> -F n=<number> \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[]
        | select(.isResolved == false)
        | select(.comments.nodes[0].body | test("projector-finding"))
        | "\(.path):\(.line)"'
```

Both loops key on the marker rather than on a login. A reply the fix loop posts
inside a thread carries `<!-- projector-reply v=1 -->` so it is never read back
as a new finding.

## Publish one review

Findings always go out as inline threads in **one** review, each carrying the
finding marker, and every review body carries the signature and verdict line.
Do not drip-feed findings or use ordinary issue comments for them. What differs
between the modes is only the review state and what marks the outcome.

**Findings open, self-review:** submit a `COMMENT` review, verdict
`changes-requested`. If the pull request is Projector-gated and not already a
draft, convert it with `gh pr ready <number> --undo`.

**Findings open, cross-author:** submit a `REQUEST_CHANGES` review, verdict
`changes-requested`. Leave draft state alone.

**Clean head, self-review:** submit a `COMMENT` review naming the exact SHA and
what was checked, verdict `approved`. If the pull request is Projector-gated,
mark it ready with `gh pr ready <number>`. That transition is the sign-off a
reader sees in the pull-request list.

**Clean head, cross-author:** submit a `COMMENT` review naming the exact SHA and
what was checked, verdict `approved`. Say plainly in the body that the head
looks clean and that a human approval is what remains. Post a real `APPROVE`
only where configuration explicitly permits it.

Never downgrade a `REQUEST_CHANGES` you could post to a `COMMENT` to work
around a permission failure, and never read GitHub's refusal of a self-review
verdict as one.

Record a SHA as reviewed only after its review is published and, in a gated
self-review, its draft or ready state is set. Verify both: re-read the review
body and the pull request's `isDraft`. Never resolve the author's findings,
claim a newer SHA was reviewed, or merge.

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
