---
name: cleanup
description: Check that a session finished everything it was asked to do, ask what to do with anything left (do it now, open an issue for it, or leave it), release what the session holds, such as its subscriptions, inbox items, reviews, worktrees, and background processes, and confirm that the session is safe to shut down. Use when the user asks to clean up, wrap up, or shut down a session.
---

# Clean up

End a session without losing work or leaving anything running. First check
that the session did everything it was asked to do. Then ask the user what
to do with each thing that is left, release what the session holds, and
confirm that the session can be shut down.

Run this skill from the conversation the user talks to, never from a
subagent: a subagent cannot stop its siblings, and its processes end when
it returns. Release only what this session created or holds. Never touch
the user's own checkout, another session's worktree, subscription, or inbox
item, or a branch or pull request the session did not create.

## Find the session's id

Projector's `project watch` and `project inbox` commands file what a session
holds under its id. Claude Code sets the id in `CLAUDE_CODE_SESSION_ID`. On
another host, it is the id the conversation chose when it first subscribed or
posted to the inbox, which `PROJECTOR_SESSION` may hold. Pass the id to every
`project watch` and `project inbox` command as `--session <id>`. On a host
that sets neither variable, `project watch list` without it lists every
session's subscriptions, and `project inbox` reads only `PROJECTOR_SESSION`
in any case. When the session never subscribed or posted, it has no id and
holds nothing under Projector's state directory.

## Take stock of the session

1. Read the repository instructions.
2. Reread the conversation and list each thing the user asked for. Add each
   thing you said you would do later, each suggestion you deferred, and each
   question you left for the user. For each item, find the evidence that it
   was delivered: a pull request URL, a commit on the remote, an answer you
   gave, or a command's output.
3. List what the session holds:

   ```sh
   project watch list --session <id> --json
   project inbox list --session <id> --json
   git worktree list --porcelain
   git stash list --format='%H %gd %gs'
   ```

   `project watch list` prints each pull request and repository the session
   fixes or reviews. `project inbox list` prints each question, notice, and
   request still open in the inbox. From `git worktree list`, keep only the
   worktrees this session created. From `git stash list`, keep only the
   entries it pushed, and note each one's SHA. Add each subagent,
   background command, monitor, `project watch stream`, and scheduled
   wakeup or job that the session started, and each temporary file it wrote
   outside its scratch directory.
4. For each worktree the session created, run `git status --porcelain` and
   `git log HEAD --not --remotes --oneline` in it. For each local branch the
   session created, run `git log <branch> --not --remotes --oneline`. Output
   from any of them means changes that exist nowhere else. Name `HEAD` or the
   branch rather than passing `--branches`, which walks every local branch in
   the repository, the user's and other sessions' included, and misses
   commits on a detached `HEAD`.
5. For each open pull request the session opened, pushed to, or reviews, run
   these commands:

   ```sh
   project review status <number> --repo <owner>/<repo> --json
   gh pr view <number> --repo <owner>/<repo> --json mergeable,statusCheckRollup
   ```

   `project review status` reports the verdict on the current head, the
   open finding threads, and each hold comment the author has open.
   `gh pr view` reports whether the pull request conflicts with its base,
   as `mergeable` reading `CONFLICTING`, and the result of each check, as a
   check run's `conclusion` or a commit status's `state`. When
   `mergeable` reads `UNKNOWN`, GitHub has not computed it yet, so ask again
   after a few seconds.
6. When the session worked on a project, run `project show <name> --json`
   and `project check`.

## Find what is unfinished

A gap is work the session owes that it has not delivered. Each of these is a
gap:

- An item from step 2 that has no evidence of delivery.
- A change that exists only in a worktree or stash: uncommitted, or
  committed and not pushed.
- A pull request that breaks the repository's hand-over rules, such as one
  without the `## Testing` section or ticket key its instructions require.
- Review work on a pull request the session fixes: an unresolved finding, a
  standing change request, a conflict with its base, failing checks, or a
  suggestion neither implemented nor deferred. `fix-pr` does this work.
- A hold comment the session posted, which says a change is still being
  built.
- A plan whose `status` or `priority` no longer matches the work, a project
  the work completed that is not marked `completed`, or a warning from
  `project check`. `finish` does this work.
- Documentation that no longer describes the behavior the session changed.
- An open inbox question or request whose answer the work still needs.

These are not gaps, because the session has no more work to do on them:

- A pull request that waits for the user's review or merge. The merge is the
  user's checkpoint, and this skill never merges.
- A draft that waits for a review by another session.
- A notice in the inbox that nothing depends on.

## Ask what to do with each gap

When there is no gap, go on to
[Release what the session holds](#release-what-the-session-holds).

Otherwise, ask one question for each gap. Say in a line what is left, and
put the evidence in the detail: what the user asked for, what exists now,
and where it lives, such as a branch, a pull request, or a worktree path.
Offer these three answers, and recommend the one that fits:

- **Do it now: we're not done.** Recommend it when the work is small and is
  part of what the user asked for.
- **Open an issue for it, then clean up.** Recommend it when the work is new
  or too large for this session.
- **Leave it: clean up and shut down anyway.** Recommend it when the work no
  longer matters, such as a deferred idea the user already declined.

When Projector's `ask` tool is available, ask there, one question per gap,
with the recommendation as its first answer. Otherwise, use the host's
question tool, or list the gaps in the chat and ask the user to answer each.
Release nothing that a gap depends on, such as the worktree that holds its
changes, until the gap's answer arrives.

When a gap is a change that exists nowhere else, say in its detail that
leaving it keeps the worktree or stash and the changes in it. Discard them
only when the user's answer says to.

## Act on the answers

- **Do it now.** Do the work with the skill that fits it: `implement` for a
  change, `fix-pr` for review work, `finish` for a plan, and `write` or
  `rewrite` for prose. Hand the work over as that skill describes. The work
  can open pull requests and add subscriptions, so start again from
  [Take stock of the session](#take-stock-of-the-session) once it is done.
- **Open an issue.** Open it in the tracker that the repository instructions
  name, or with `gh issue create` when they name none. First search the
  tracker for an issue that already covers the work, such as with
  `gh issue list --search "<words>"`, and add to that issue instead of
  opening a second one. Write the issue for someone who never saw this
  conversation, as the writing guide in `../writing.md` describes: what is
  left, why it matters, where the work stands, and how to tell it is done.
  Before you post, check the repository's visibility with
  `gh repo view <owner>/<repo> --json visibility`, and follow the
  repository instructions on what an issue may name. Put nothing from a
  private repository into an issue in a public one. Keep each issue's URL
  for the report.
- **Leave it.** Do nothing more for the gap, and name it in the report.

When a gap held a hold comment and the user chose an issue or to leave it,
end the hold with `project review hold <number> --drop`. A hold that no
session will lift keeps the pull request a draft however clean its review.

## Release what the session holds

Release each kind of resource in this order, so that nothing still running
recreates what you released.

1. **Subscriptions.** For each line `project watch list` printed, run
   `project watch remove <target> --role <role> --session <id>`, adding
   `--model <model>` for a `review` line. Another live session that
   subscribes to the same pull request then owns its role at once. Skip each
   line that `project watch list` marks `excluded: true`, a `!` line that
   keeps a pull request the user dismissed out of the session.
   `project watch remove` leaves those lines, and they deliver nothing.
   Remove the subscriptions first, because a live one can announce a new
   head and start another review or fix after you stop the subagents.
2. **Subagents and background work.** Stop each subagent, background
   command, monitor, `project watch stream`, and scheduled wakeup or job the
   session started. A fixing subagent subscribes again after each push, so
   once they have stopped, run `project watch list --session <id> --json`
   again and remove any line a subagent added.
3. **Reviews.** For each pull request the session reviews, finish its review
   so another session can take it over at once, as
   `../review-repo/SKILL.md` describes under "Stop reviewing the repository".
   `project review finish` removes the review's scratch worktrees, so they
   need no step of their own.
4. **Inbox items.** Withdraw each item the session still has open, with
   Projector's `withdraw` tool or with
   `project inbox withdraw <number> --session <id>`. No session will read
   the answer once this one shuts down. Name each withdrawn question or
   request in the report, so nothing the user meant to answer is lost.
5. **Worktrees.** Remove each worktree the session created that holds no
   change that exists nowhere else. Leave it first when the session's
   working directory is inside it. Remove it with the host tool that created
   it, such as Claude Code's `ExitWorktree` for a worktree that
   `EnterWorktree` made, or otherwise with `git worktree remove <path>` and
   then `git worktree prune`. Never pass `--force` unless the user said to
   discard the worktree's changes.
6. **Branches.** Delete a local branch the session created only when its
   pull request has merged or closed, or when the user said to discard it.
   Keep the branch of an open pull request.
7. **Stashes and temporary files.** Drop each stash entry the session
   pushed and no gap kept. Another session can shift an entry's
   `stash@{n}` position, and `git stash drop` refuses a SHA, so find the
   entry's current reference by its SHA just before you drop it:

   ```sh
   git stash list --format='%H %gd'
   git stash drop 'stash@{<n>}'
   ```

   Delete each temporary file the session wrote outside its scratch
   directory.

## Confirm that the session can shut down

Run the commands from
[Take stock of the session](#take-stock-of-the-session) again. Expect no
line from `project watch list` but those marked `excluded: true`, no open
item from `project inbox list`, and no worktree, stash entry, or running
process the session started, except what a gap's answer kept.

Then report, as a list with one entry per thing:

- **Done:** each thing the user asked for, with its evidence, such as a pull
  request URL.
- **Filed:** each issue you opened or added to, with its URL.
- **Left:** each gap the user chose to leave, and each worktree, branch, or
  stash you kept, with its path or name.
- **Waiting on you:** each pull request that waits for the user's review or
  merge. No session fixes its findings after this one shuts down, so say
  that the user can run `fix-pr` on it from another session.
- **Released:** what you stopped, removed, and withdrew.
- **Withdrawn from the inbox:** each question or request that was still open.

End with one line that says the session is safe to shut down, such as
"Everything is wrapped up, and you can shut this session down." Say it only
when the check above passed. When something could not be released, say what
and why instead, and do not say that the session is safe to shut down.
