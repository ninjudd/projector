---
name: start-fix-loop
description: Watch chat-owned GitHub pull requests for review findings and merge conflicts, and run fix-pr on each one that has work, one subagent per pull request. Use when the user asks to keep fixing review feedback as it arrives or drive PRs to a clean review.
---

# Start Fix Loop

Run a persistent background loop alongside foreground work. Watch only pull
requests this conversation created or explicitly adopted, and keep watching
until the user stops the loop, or, for a loop `implement` started, until every
pull request it watches has merged or closed. The loop decides when a pull
request has work; the work itself is the `fix-pr` skill, in
`../fix-pr/SKILL.md`, run for that pull request. That skill defines the
operator, what counts as outstanding work, how to merge a conflicting base,
and how to verify, fix, reply, and resolve, so this file covers only the loop
around it.

## Resolve the operator and scope

Resolve the operator as `../fix-pr/SKILL.md` describes, once for the loop.

Run the loop only in the top-level conversation, the one the user talks to.
A subagent's processes end when it returns, and the conversation that
started it cannot see a loop it left behind, so a subagent never starts or
joins a fix loop. A subagent asked to start one hands the request up
instead, and a subagent that opens a pull request, such as one running
`implement`, hands the pull request up. The conversation that started it may
be a subagent too, so each one hands it up in turn until it reaches the
top-level conversation. There a pull request counts as one this conversation
created.

Watch only the repository containing the current working directory. Covering a
second repository requires starting this skill from that repository with its
own tracked set and watcher state.

The tracked set is not every operator pull request. Build it from pull
requests created by this conversation plus any pull request the user
explicitly assigns, and write it to the tracked file the watcher reads, one
`owner/repo#number` per line. That file is the watcher's whole scope: it
lists nothing and discovers nothing, so a pull request outside the file
produces no event however loud its threads are, and a newly opened pull
request joins only when this conversation appends it because it created or
adopted it. Never list a pull request you would not push to.

## Establish the loop

1. Read repository instructions, `AGENTS.md` or `CLAUDE.md`,
   `../fix-pr/SKILL.md`, and applicable GitHub skills.
2. Resolve the repository, checked-out branch, exact tracked pull request set,
   each base and head SHA, author, state, mergeability, review decision, and
   unresolved review threads. Include every layer of a stack created by this
   conversation, found as `../implement/SKILL.md` describes under "Stack
   dependent work". Baseline only already-resolved threads. Never baseline an
   unresolved thread; every finding already waiting when the loop starts
   remains outstanding.
3. Verify the operator can push each tracked branch. Do not test with an empty
   commit or direct push.
4. Write the tracked set to a durable tracked file, one `owner/repo#number`
   per line, kept beside the state file and outside any checkout. That file
   is the record of the set. Append a pull request when this conversation
   creates or adopts one, delete its line when it closes or merges, and
   record the file's path in a persistent recurring goal so a later turn
   edits the same file. The watcher re-reads it every cycle, so neither
   change needs a restart.
5. Run one pass first:

   ```sh
   scripts/watch-threads.sh --tracked <file> --state <state-file> --once
   ```

   It refuses to start, naming the line, on an entry that is not
   `owner/repo#number` or a number that names no pull request, and it
   distinguishes a lookup that failed for network or auth reasons from a
   missing pull request. Compare its output with the unresolved threads,
   verdicts, body-only reviews, and conflicts you fetched in step 2: work
   missing from the output is a pull request missing from the file, and an
   empty output over outstanding work is a file that lists the wrong set.
   The pass records what it announced, so the running watcher repeats that
   work only at the renotification interval; dispatch it from this output.
6. Run the same script without `--once`, with the same state file, an
   interval of at least 30 seconds, and a finite renotification interval,
   under the host's persistent process or monitor facility. Every event it
   emits names a pull request from the tracked file, so act on each one. A
   `TRACKED` line is a problem with the file itself, announced once, and the
   fix is to the file rather than to a pull request. When `implement` starts
   the loop, add `--exit-when-closed`. When the user asks for a loop while
   one `implement` started is running, restart its watcher without that
   flag, so it stays idle on an empty file as a loop the user started does.

Before adopting an existing watcher, fetch unresolved threads, verdicts,
body-only reviews, and mergeability directly. Confirm its tracked file is the
set this conversation owns -- a watcher another session started watches that
session's set -- and verify its state file contains every expected unresolved
row. Restart the watcher when its script changed after it started; a live
process cannot prove which file version it loaded.

The watcher is level-triggered:

- `FINDING` reports an unresolved inline thread.
- `VERDICT` reports a real `CHANGES_REQUESTED`, including a review whose
  findings exist only in its body.
- `CONFLICT` reports a branch that conflicts with its base. It clears when a
  push makes the branch mergeable again.
- `REVIEW` reports a review body that has no inline thread, the shape every
  `COMMENT` review posts. A Projector review whose body lists `Suggestions`
  is work even under a clean verdict, because `fix-pr` implements or defers
  each one.
- `DRAFT` reports a pull request a review loop has not signed off. It clears
  when the pull request is marked ready, which a review loop's `publish`
  does when the newest verdict on the current head is clean, whichever model
  gave it, no finding thread is open, and no hold is open.
- `TRACKED` reports a line in the tracked file the watcher cannot use, once.
- `DONE` reports, under `--exit-when-closed`, that every tracked pull request
  has merged or closed, and the watcher exits after it. A pass in which a
  query failed or the file had a bad line never reports it.

A `DRAFT` line alone is not work. It says the head has not been signed off,
and any work behind it arrives as its own `FINDING`, `VERDICT`, `CONFLICT`, or
`REVIEW` line. A draft with nothing else outstanding is waiting on the review
loop to re-review, not on a code change. A pull request that a subagent's
hold keeps in draft reports `DRAFT` too, and its work is already with that
subagent: the changes the hold lists.

## Give each pull request its own subagent

Where the host can keep a subagent alive and send it later messages, each
tracked pull request gets one fixing subagent for as long as it stays open.
The main loop owns the watcher and the tracked file; the subagent owns every
fix on its pull request. Fix context that carries over is the point: on a new
round the subagent already knows the findings it answered, the ones it
declined and why, and the commits it pushed, so it reads a reopened thread as
the recurrence it is instead of as new work.

- On a pull request's first `FINDING`, `VERDICT`, `CONFLICT`, or `REVIEW`,
  start its subagent with a name that carries the number, such as
  `fix-<number>`. Give it the repository, the pull request, the operator
  login, the event lines, and the path of `../fix-pr/SKILL.md`, and have it
  run that skill for the pull request.
- Give a stack one subagent for all its layers, named `fix-stack-<number>`
  for the bottom layer, so the name says it covers more than that one pull
  request. A fix to a lower layer is carried up into every layer above it,
  so two subagents working one stack would push the same branches. Group
  tracked pull requests into stacks by the same lookup as step 2. It reads
  base branches when GitHub records no stack, so a chain opened without
  `gh stack` still gets one subagent.
- Have each subagent work in its own worktree of its pull request's branch,
  so two subagents never share a checkout and none touches the main loop's.
- Send every later event for that pull request, or for any layer of its
  stack, to the same subagent as a message; never start a second one for it.
  Each message is another run of `fix-pr` for the pull request's current
  head.
- Send a change the owner asks for on a tracked pull request to its subagent
  the same way, starting the subagent if the pull request has none yet.
  `fix-pr` holds the pull request in draft while it builds the change, as
  `../fix-pr/SKILL.md` describes under "Hold the pull request while changes
  are built".
- The subagent reports each run back: the head SHA, each layer's new head
  when it carried a change up a stack, the merge commit if it made one, the
  fix commits, the declined findings, the suggestions it implemented, each
  one it deferred with its reason, any hold it posted, edited, or lifted,
  and the validation results. A question for the user comes back to the
  main loop to ask, and the subagent goes on with the pull request's
  unrelated work meanwhile.
- When a pull request merges or closes, delete its line from the tracked
  file at once. Tell its subagent to finish, remove its worktrees, and close
  only when no layer it owns is still open. The user merges a stack as a
  unit, the whole of it or any number of layers from the bottom up, so when
  only the bottom layers merge, the `fix-stack-<number>` subagent keeps its
  name and goes on fixing the layers left open. It starts with the update
  the lowest of them needs onto its new base, as `../fix-pr/SKILL.md`
  describes under "Preserve stacked ownership".
- When a subagent is lost, to a session restart for example, start a
  replacement under the same name. It rebuilds its context from GitHub: each
  open layer's review threads, the replies on them, and its review bodies.

The main loop routes events and records outcomes; it does not inspect code,
so its own context stays small however many pull requests it tracks. Where
the host has no subagents, the main loop runs `fix-pr` itself for each pull
request with work, in the order the events arrived.

## Report clean state and continue

After each subagent report, check the pull request against the clean
conditions in `../fix-pr/SKILL.md` and report its state with the head SHA,
fix commits, declined findings, deferred suggestions, and validation
results, then keep watching. A pull request with an open hold comment is not
clean, however clean its review: `project review status` reports it `held`.
Say so, and why: "#12 is held in draft while 2 changes are implemented",
naming the changes the hold lists.

Report a stack's readiness as one unit, the same unit its subagent fixes,
because the user merges it as one from GitHub. Check every open layer
against GitHub itself: its clean conditions, and GitHub's comparison of it
with the layer below, which `../implement/SKILL.md` describes under "Stack
dependent work". Never take sync from a local `gh stack view`, which reads
only one checkout's branches. Say "stack #<number> is ready to merge as a
whole" when every layer's current head is clean and every comparison
prints `0`. Otherwise name the bottom layers that are ready, such as "#12
and #13 at the bottom of stack #14 are ready to merge", and what holds back
the lowest layer that is not. A hold on a layer holds the stack from that
layer up, because the user merges from the bottom, so name the held layer
and the changes its hold lists: "#12 at the bottom of stack #14 is ready to
merge; the stack is held at #13 while 2 changes are implemented". Name the
stack by the number GitHub's stacks API gives it, or by its bottom pull
request for a chain of base branches that GitHub does not record as a
stack.

Delete closed or merged pull requests from the tracked file. An empty file is
idle, not an instruction to stop; remain ready to append pull requests this
conversation creates or adopts later. A loop `implement` started is the
exception: its watcher reports `DONE` and exits once every pull request it
tracks has merged or closed. The watcher announces no merge, so `DONE` can
be the first the loop hears of one, and the file can name a pull request
appended after the watcher last read it. On `DONE`, check the state of each
pull request the tracked file names:

```sh
gh pr view <number> --repo <owner>/<repo> --json state --jq .state
```

Delete the line of each one that merged or closed. When one is still open,
or a check fails, start the watcher again with `--exit-when-closed`; it ends
the watch itself once they have all closed. Otherwise finish each subagent
as a merged pull request's subagent finishes, report that the loop stopped
because its work is done, and leave the next `implement` to start a new one.

Beyond that, stop a loop only when the user asks, a required user decision
blocks one finding, validation cannot be restored, or every remaining
finding on a pull request is deliberately declined.

## Never

- Never merge.
- Never list a pull request in the tracked file that this conversation did not
  create and the user did not assign.
- Never let two subagents push to the same branch.
- Never claim a watcher, review, or clean state without checking live evidence.
