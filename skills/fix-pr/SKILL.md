---
name: fix-pr
description: Fix one pull request's outstanding review work once. Merge its base when the branch conflicts, verify each finding, fix and push the valid ones, reply to and resolve their threads, and decline the rest with evidence. Use when the user asks to fix, address, or answer review feedback on a pull request, or to resolve its merge conflicts.
---

# Fix a pull request

Bring one pull request's current head to where a review can sign it off.
Merge its base when the branch conflicts, and answer every outstanding
finding with a pushed fix or a declined reply. The fix runs once and starts
no watcher. `start-fix-loop` runs this skill whenever its watcher reports
work on a pull request.

## Choose the pull request

Take the pull request from the user: a URL, or a number in the repository
containing the current working directory. When the user names none, use the
open pull request for the checked-out branch:

```sh
gh pr view --json number,url,state,isDraft,headRefOid,baseRefName
```

When that finds no pull request, or finds one that is not open, ask the user
which pull request to fix rather than guessing.

Work in a checkout of the pull request's head branch that has no uncommitted
changes, such as a worktree of that branch. Never discard or overwrite
uncommitted work to switch branches.

## Resolve the operator

The **operator** owns the pull request's branch and is the identity allowed
to push fixes. Resolve that login from explicit user input, then repository
instructions. Confirm it with `gh auth status` and `gh api user --jq .login`
under the token used for pushes.

The operator is deliberately not a `.projector.toml` setting, though other
Projector behavior is. Every fix is pushed and every reply posted through the
operator's token, so a file naming a different account could only disagree
with the identity actually doing the work. Ownership follows the token, not a
file that can disagree with it.

A review loop may run under a different reviewer identity. If the ambient
identity is not the operator, do not push through it. Hold the validated
operator login literally; never use `@me`. Push only to a branch the operator
owns: being able to see a branch does not authorize pushing to it.

## Find the outstanding work

Fetch the pull request's state, its unresolved review threads, and its review
bodies:

```sh
gh pr view <number> --json state,isDraft,mergeable,baseRefName,headRefName,headRefOid,reviewDecision
gh api repos/<owner>/<repo>/pulls/<number>/reviews --paginate
```

Read the threads from GraphQL's `reviewThreads`, because only it says which
are resolved. The work outstanding on the head is:

- **A merge conflict**, when `mergeable` is `CONFLICTING`. `UNKNOWN` means
  GitHub has not computed it yet, so ask again after a few seconds.
- **Every unresolved review thread.**
- **A standing `CHANGES_REQUESTED`** from a cross-author reviewer, including
  one whose findings exist only in its body.
- **A body-only finding** in a `COMMENT` review.

Findings arrive from two kinds of reviewer and both are answered the same way.
A **cross-author** reviewer — a teammate, Codex, Bugbot, or a review loop run by
someone else — posts real `CHANGES_REQUESTED` verdicts. A **self-review** loop,
which GitHub allows only to post `COMMENT` reviews on the operator's own pull
requests, records its outcome in the review body's verdict line and in draft
state instead. Read both signals; never treat an unmoved `reviewDecision` as
evidence that nothing is outstanding.

Read review bodies and compare them with the thread set. A Projector review
body carries a verdict line; read it rather than inferring the outcome. Dispose
of a body-only finding in a pull-request-level comment because there is no
thread to reply to or resolve.

A `Suggestions` list in a Projector review body is not a body-only finding,
and no item on it is outstanding work. Weigh each item yourself: take it when
the improvement is worth a push and the review cycle that follows, and leave
it when it is not. A clean verdict above such a list is a clean head either
way, and an item you leave needs no reply.

Draft state is not itself a finding. It says the head has not been signed
off, so look for the work in the threads and review bodies. A draft with no
outstanding finding is waiting on the review loop to re-review, not on a code
change. When nothing is outstanding, say so and stop.

## Merge the base when the branch conflicts

Resolve a conflict before you fix findings. Each fix is verified against the
head the merge produces, and that head is the one the next review reads.

1. Fetch, and merge `origin/<baseRefName>` into the branch. Merge rather than
   rebase unless the repository's instructions say otherwise: a merge needs
   no force-push, so review threads stay anchored to their commits. A layer
   of a stack is the exception while `gh stack` works, because `gh stack`
   keeps a stack current by rebasing it: run `gh stack rebase` and then
   `gh stack push`, and resolve a conflict the rebase stops on as
   `../gh-stack/SKILL.md` describes under "Handle rebase conflicts". A
   stacked pull request's base is its parent's branch until the parent
   merges. GitHub then retargets it to the default branch, but only when the
   merge deletes the parent's branch. Where the repository keeps merged
   branches, retarget the child yourself with
   `gh pr edit <number> --base <default-branch>`.
2. Resolve each conflicted file by reading both sides and the commits that
   made them, with `git log --merge -p <file>`. Keep what each side meant to
   do. When one side already contains the other's change, take that side.
   Regenerate a generated or compiled file with its build command instead of
   editing it by hand, as the repository's instructions describe. When the
   two sides make choices only the user can reconcile, stop and ask.
3. Run the repository's full test, lint, format, documentation, and
   validation gate. A merge without textual conflicts can still break, for
   example when one side renames a function and the other adds a call to it.
4. Commit the merge, push the branch, and confirm that `mergeable` reads
   `MERGEABLE`.

A conflict has no thread to reply to, so the merge commit is its record.
Re-read the pull request body's `Testing` commands afterwards, because a merge
can change a count or a path they name.

## Verify and fix findings

Handle findings in posting order, batching only related findings that touch the
same code:

1. Verify the claim against the exact pushed head and surrounding code by the
   four-step protocol in `../review-pr/method.md` § 5: quote the
   lines, walk the execution, write the triggering sequence, and look for
   what already stops it. Decline a false finding by naming that guard with
   the quoted code that shows it, rather than changing correct behavior.
2. Reproduce a valid defect with a failing test, error, or measurement.
3. Implement the narrow fix in the branch that owns the code, following
   `../guidelines.md`, the code guidelines the review read the head against.
   A finding that cites a section of that file is verified against that
   section, and declined only by showing the rule does not apply to the
   quoted code. Read the lines around an insertion anchor before editing so
   an attribute, decorator, or comment is not silently detached.
4. Prove a regression test fails without the fix and passes with it.
5. Run the repository's full test, lint, format, documentation, and validation
   gate.
6. Keep independent fixes in independent commits; batch findings that share one
   cause.

If the correct behavior requires a user decision, ask instead of guessing and
go on with unrelated findings. If a finding recurs after a pushed fix, stop
and report the recurrence before changing it again.

## Commit, reply, push, resolve

Follow the writing guide in `../writing.md` when you write each commit message
and thread reply. For every accepted finding, preserve this order:

1. Commit the validated fix locally.
2. Reply in its review thread under the operator identity, opening the reply
   with `<!-- projector-reply v=1 -->` so a loop sharing this login never reads
   it back as a new finding. Name the commit and state what was reproduced and
   changed.
3. Push the pull request branch, never its base branch.
4. Resolve the thread only after the push succeeds.
5. Re-fetch `reviewThreads` and confirm `isResolved: true`.

Resolving says the fix is pushed; it is not the final word on a Projector
finding. The review loop verifies every one of its findings on the next head,
resolves any you left open, and reopens one whose fix does not hold, replying
with `<!-- projector-verify v=1 result=reopened -->`. A reopened thread is the
recurrence above, not new work.

Resolve a person's thread the same way once its fix is pushed, so a reviewer
who never resolves threads does not leave the pull request looking
unanswered. Where `project config get fix.resolve_human_threads --default
true` prints `false`, reply to a person's thread and leave resolving it to
them. A Projector finding or another tool's thread is resolved either way.

Reply before pushing so a reviewer triggered by the push sees the reasoning,
but push promptly because the named commit is briefly local-only. If a push
fails, post that fact, leave the thread unresolved, and report the blocker.

Fixing an external reviewer's finding is not a reason to ask that reviewer for
another pass. Codex, Cursor, and Bugbot re-review on push by themselves, so the
request buys nothing and spends someone's money to add noise to the pull
request. This is the moment the temptation is strongest, because the fix looks
incomplete until somebody confirms it; the push is the confirmation.

Do not wait for one either. Those services are slow beside a local loop, and
their findings are worth fixing without their latency being worth inheriting.
Fix what they raise, push, and carry on; whether their re-review has landed is
not this skill's business.

Never resolve a finding you declined or could not fix. An open declined thread
is the user's merge decision. Re-read and rerun the pull request body's
`Testing` commands after each fix batch, and update stale claims in unwrapped
GitHub prose.

## Preserve stacked ownership

Fix code on the stack branch that introduced it. Find the layers above it,
and carry each fix up through every one of them, as `../implement/SKILL.md`
describes under "Stack dependent work": with `gh stack rebase --upstack` and
`gh stack push` where `gh stack` works, and from base branches where it does
not. Then rerun each affected layer's full gate. Do not patch parent code
inside a child merely to avoid rebasing.

After a parent merges, run `gh stack sync`, which rebases the layers above
onto the new base and drops a squash-merged parent's commits from them as
`../gh-stack/SKILL.md` describes under "Squash-merge recovery". Then verify
each child points at the intended base and remains mergeable. Where
`gh stack` cannot sync the stack and the parent was squash-merged, merge the
new base into the child even though `mergeable` reads `MERGEABLE`: until then
the child's diff still carries the parent's original commits, and no conflict
event will say so. Never merge any layer; merging remains the user's
checkpoint.

## Report the result

A pull request is clean only when:

- it merges into its base without a conflict,
- a clean verdict names its current head,
- no unresolved threads remain,
- the current head is the one actually reviewed, and
- the reviewer's own sign-off signal is clear: `reviewDecision` is not
  `CHANGES_REQUESTED` for a cross-author reviewer, and a self-reviewed pull
  request is no longer a draft, which is how that loop signs off.

An external reviewer is not one of those conditions. A pending or absent
re-review from Codex, Cursor, or Bugbot does not hold a head back from clean:
report on the review loop's verdict and let theirs arrive whenever it does. A
standing `CHANGES_REQUESTED` from one is a real verdict and does block, exactly
as any other cross-author verdict would.

Report the head SHA, the merge commit if you made one, the fix commits, the
declined findings, the Suggestions you took and left, and the validation
results. A stale `CHANGES_REQUESTED`, or a pull request still in draft after
all fixes, is a wait for re-review rather than another code change. Do not
manufacture an empty commit to trigger it.

Never mark a draft pull request ready yourself. A clean review is what clears
a draft, so undrafting by hand sends the work to human reviewers carrying a
sign-off nothing gave it. If you opened it as a draft, you do not undraft it —
not when the reason you drafted it is resolved. That is the case that feels
justified: the red test you drafted around is passing, so the draft reads as
stale state left over from a fixed problem. It is not. It is a standing
request for review that has not been answered yet.

## Never

- Never merge the pull request. Merging its base into its branch is how you
  resolve a conflict; merging the pull request is the user's checkpoint.
- Never push through the reviewer identity or to an unowned branch.
- Never discard or overwrite uncommitted work to switch branches.
- Never force-push except to push a stack's rebased layers, or to resolve a
  conflict where the repository's instructions call for a rebase, and use
  `--force-with-lease` either way.
- Never close the pull request and open a replacement to escape a conflict.
  That discards its review threads; fix the branch in place.
- Never claim a review or clean state without checking live evidence.
- Never invoke an external reviewer unless the user names it, and never
  re-request one after fixing its finding.
