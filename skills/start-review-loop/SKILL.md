---
name: start-review-loop
description: Monitor the operator's pull requests in the current GitHub repository and run review-changes on every pushed SHA, one subagent per pull request, so each head gets an exact-head review and a pull request stays in draft until its head is clean. Use when the user asks to keep reviewing current and subsequent PR heads.
---

# Start Review Loop

Run a persistent exact-head review loop for the current repository. The loop
watches pull requests and decides when a head needs a review; each review is
the `review-changes` skill, in `../review-changes/SKILL.md`, run once for that
head. That skill defines the reviewer and operator identities, the two review
modes, what a review inspects, and how it publishes, so this file covers only
the loop around it.

## Resolve the operator and scope

Resolve the reviewer and the operator as `../review-changes/SKILL.md`
describes; the watcher needs the operator's login for `--author`.

Watch only the repository containing the current working directory. The loop
reviews every open pull request the operator authors there, from whichever
session opened it, plus any the user explicitly assigns. The tracked set lives
in the tracked file the watcher reads, one `owner/repo#number` per line, and
the watcher sees nothing outside it. Run the watcher with `--repo <owner/repo>
--author <operator>` and it appends each operator pull request it finds open
to that file, so a new one reports as `NEW PR` without anyone adopting it. A
line `!owner/repo#number` keeps one pull request out when the user asks. Hold
validated logins literally in every query you run yourself; never use `@me`.
Under the default the two roles coincide and `@me` looks harmless, but its
value follows whichever token is live, so under a reviewer override it
resolves to the reviewer — an account that authors nothing here — and a
listing by `@me` comes back empty, indistinguishable from a repository with
nothing open.

## Establish the loop

1. Read repository instructions, `../review-changes/SKILL.md`, and applicable
   review or GitHub workflow skills.
2. Resolve the repository, checked-out branch, the operator's open pull
   requests, any the user assigned, and the total open count:

   ```sh
   gh pr list --author <operator> --state open --limit 200
   ```

3. Fetch each pull request's base SHA, head SHA, state, reviews, and
   thread-aware review threads. Say how many operator pull requests are tracked
   out of the repository total.
4. Determine the last reviewed SHA from durable GitHub state or the current
   conversation. Review any current head without a completed exact-head review
   immediately; do not baseline it away.
5. Write the tracked set to a durable tracked file, one `owner/repo#number`
   per line, kept beside the state file and outside any checkout. That file
   is the watcher's whole scope and the record of the set. The watcher
   appends the operator's pull requests itself; append one the user assigns,
   and delete a line when its pull request closes. Start or reuse a
   persistent recurring goal that records the repository, operator, the
   tracked file's path, and every reviewed SHA paired with the id of the
   review this loop published for it.
6. Seed the state file with only the SHAs already reviewed, then run one
   pass:

   ```sh
   scripts/watch-prs.sh --tracked <file> --state <seeded-file> \
     --repo <owner/repo> --author <operator> --once
   ```

   It refuses to start, naming the line, on an entry that is not
   `owner/repo#number` or a number that names no pull request, and it
   distinguishes a lookup that failed for network or auth reasons from a
   missing pull request. Every head you found unreviewed in step 4 must come
   back as `NEW PR`; one that does not is a pull request missing from the
   file. The pass records those heads as announced, so the running watcher
   will not repeat them; review them from this output. Then run the same
   script without `--once`, with the same state file and adoption flags,
   `--worktree <repository>`, and an interval of at least 30 seconds, under
   the host's persistent process or monitor facility.

The watcher prints `NEW PR`, `NEW HEAD`, `RESPONDED`, `CLOSED`, `BRANCH`, and
`TRACKED`. Treat `RESPONDED` as a re-review request for a still-draft head
whose threads are all resolved: the author answered without pushing, so no
head event is coming, and only a re-review can sign off and mark it ready. It
prevents a body-only response from deadlocking both loops. A `TRACKED` line is
a problem with the tracked file itself, announced once; fix the file. Keep the
loop silent while no event needs action.

If adopting an existing watcher, fetch unresolved state first, confirm it runs
with the adoption flags for this repository and operator and reads the tracked
file this loop records, and verify its state file contains every expected row.
Restart it when the script changed after the process started. Silence alone
proves nothing.

## Give each pull request its own subagent

Where the host can keep a subagent alive and send it later messages, each
tracked pull request gets one reviewing subagent for as long as it stays open.
The main loop owns the watcher, the tracked file, and the record of reviewed
SHAs; the subagent owns every review of its pull request. Review context that
carries over is the point: on a fix-cycle head the subagent already knows its
earlier findings, the author's replies, and what it verified last round, so it
reads the new head against that history instead of rediscovering it.

- On a pull request's first `NEW PR`, start its subagent with a name that
  carries the number, such as `review-<number>`. Give it the repository, the
  pull request, the head SHA, the reviewer and operator logins, the review
  mode, the review ids this loop has recorded for the pull request, and the
  path of `../review-changes/SKILL.md`, and have it run that skill for the
  head.
- Send every later `NEW HEAD` and `RESPONDED` for that pull request to the
  same subagent as a message; never start a second one for it. Each message
  is another run of `review-changes`, for the head it names. A `NEW HEAD`
  that arrives mid-review is the moved-head case that skill's start-comment
  rule covers.
- The subagent reports each published review back: the SHA, the verdict, the
  review id, and how many threads it opened. The main loop records the SHA
  and review id. A question for the user comes back to the main loop to ask.
- Between heads the subagent keeps its scratch worktree; on `CLOSED`, tell it
  to finish: delete any start comment it still holds and remove its scratch
  worktrees. Then close the subagent and delete the pull request's line from
  the tracked file.
- When a subagent is lost, to a session restart for example, start a
  replacement under the same name. It rebuilds its context from GitHub: the
  pull request's Projector reviews, their finding threads, and the replies on
  them.

The loop's record decides the collision check `review-changes` runs before it
publishes. An id in the record is this loop's own, including the second
verdict a `RESPONDED` re-review legitimately publishes on an unmoved head, so
it never trips the check. An id absent from it means another loop under the
same account is reviewing this pull request: hold the review and ask the user
which loop continues. The tracked file does not prevent this: every loop that
adopts the same author's pull requests tracks the same ones.

The main loop routes events and records outcomes; it does not inspect code, so
its own context stays small however many pull requests it tracks. Where the
host has no subagents, the main loop runs `review-changes` itself for each
head.

## Continue after fixes

Every pushed SHA, including a fix-only SHA, starts a complete review cycle.
Green CI and resolved threads are evidence about state, not substitutes for
review. When a pull request closes or merges, close its subagent and delete
its line from the tracked file. The watcher adopts the operator's new pull
requests itself; append one the user assigns, and continue until the user
stops the loop.
