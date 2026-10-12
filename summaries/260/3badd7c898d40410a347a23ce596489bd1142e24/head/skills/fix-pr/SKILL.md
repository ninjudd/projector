---
name: fix-pr
description: Fix one pull request's outstanding review work once. Merge its base when the branch conflicts, verify each finding, fix and push the valid ones, reply to and resolve their threads, decline the rest with evidence, and implement or defer each suggestion, holding the pull request in draft while a change is built. Use when the user asks to fix, address, or answer review feedback on a pull request, or to resolve its merge conflicts.
---

# Fix a pull request

Bring one pull request's current head to where a review can sign it off.
Merge its base when the branch conflicts, and answer every outstanding
finding with a pushed fix or a declined reply. Implement or defer each
suggestion a review lists, and hold the pull request in draft while you
build a change no review has read. The fix runs once and starts no watcher.
`start-fix-loop` runs this skill whenever its watcher reports work on a pull
request.

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
- **Every suggestion you have not decided** in the newest Projector review
  of the current head by each model, as
  [Decide each suggestion](#decide-each-suggestion) describes.
- **A change the owner asks for** in the conversation.
- **Your open hold comment.** The changes it lists are outstanding until a
  push carries them, and the comment itself until you end the hold, as
  [Hold the pull request while changes are built](#hold-the-pull-request-while-changes-are-built)
  describes.

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

A `Suggestions` list in a Projector review body is not a body-only finding.
Its items never hold a head back from clean and need no reply, but you
decide each one.

Draft state is not itself a finding. It says the head has not been signed
off, so look for the work in the threads and review bodies. A draft with no
outstanding work is waiting on the review loop to re-review, not on a code
change. When nothing is outstanding, say so and stop.

## Merge the base when the branch conflicts

Resolve a conflict before you fix findings. Each fix is verified against the
head the merge produces, and that head is the one the next review reads.

1. Fetch, and merge `origin/<baseRefName>` into the branch. Merge rather than
   rebase unless the repository's instructions say otherwise: a merge needs
   no force-push, so review threads stay anchored to their commits. A layer
   of a stack is the exception while `gh stack` works: `gh stack` keeps a
   stack current by rebasing it and has no option to merge, so its rebase is
   how the layer takes in its base, the default branch included. Run
   `gh stack rebase` in place of the merge, as `../implement/SKILL.md`
   describes under "Stack dependent work", and push nothing yet; merge only
   where `gh stack` is unavailable. A stacked pull request's
   base is its parent's branch until the parent merges. GitHub then
   retargets it to the default branch, but only when the merge deletes the
   parent's branch. Where the repository keeps merged branches, retarget the
   child yourself with `gh pr edit <number> --base <default-branch>`.
2. Resolve each conflicted file by reading both sides and the commits that
   made them, with `git log --merge -p <file>`. Keep what each side meant to
   do. When one side already contains the other's change, take that side.
   Regenerate a generated or compiled file with its build command instead of
   editing it by hand, as the repository's instructions describe. When the
   two sides make choices only the user can reconcile, stop and ask.
3. Run the repository's full test, lint, format, documentation, and
   validation gate, on every layer the rebase rewrote for a stack. A merge
   or rebase without textual conflicts can still break, for example when one
   side renames a function and the other adds a call to it.
4. Commit the merge, push the branch, and confirm that `mergeable` reads
   `MERGEABLE`. A stack's rebase leaves no merge to commit: once the gate
   passes on every rewritten layer, push them all with `gh stack push`, and
   confirm `mergeable` on each.

A conflict has no thread to reply to, so the merge commit is its record.
Re-read the pull request body's `Testing` commands afterwards, because a merge
can change a count or a path they name.

## Decide each suggestion

A Projector review lists its P3 items, changes the code is correct without,
under a `## Suggestions` heading in its body. A suggestion never holds a head
back from clean, so a review can sign a head off and mark the pull request
ready above a list of them. Each one still gets a decision: you implement it
or you defer it. Decide every suggestion in the newest Projector review of
the current head by each model. An incremental review lists none, so read
its suggestions from the full review it builds on, which its lead links.

- **Implement** a suggestion that is small and clearly right. That is the
  default. Verify it first, as you verify a finding under
  [Verify and fix findings](#verify-and-fix-findings): quote the lines it
  names and confirm that the change it describes is correct there. Then hold
  the pull request, as the next section describes, and build the change the
  way you build a fix, through the full gate.
- **Defer** a suggestion that is large, reaches beyond this change, needs a
  decision you cannot make, or does not hold up when you verify it. Record
  it in your report with a one-line reason, and post nothing about it on the
  pull request.

An instruction from the owner in the conversation overrides the default, in
either direction: "defer the suggestions" or "take the second one". A
suggestion you deferred on an earlier head stays deferred when a later
review lists it again, unless the owner says otherwise.

Neither decision needs a reply. A suggestion has no thread, so the push that
carries an implemented one, and the review of that push, are its record.

## Hold the pull request while changes are built

A review that signs a head off marks the pull request ready, and from then
on the owner can merge it. A change you make after that review is one it
never read: a suggestion you implement, or a change the owner asks for. So
the pull request must not look ready to merge while you build that change.
Hold it with `project review hold` before you start. Run the command as the
operator, who authored the pull request; it refuses any other account,
because only the author's hold counts. Pass `--repo <owner>/<repo>` every
time, so the command acts on the pull request's repository from any
directory. Without it, the command acts on the `origin` of the checkout you
run it in, and refuses outside one.

1. Hold the pull request, with one `--add` for each change you are about to
   build:

   ```sh
   project review hold <number> --repo <owner>/<repo> \
     --add "Skip fenced code blocks when the packaging test looks for headings" \
     --add "Name the held layer in the stack's readiness report"
   ```

   The command posts one comment in this shape, reads it back, and confirms
   that the marker is its last line:

   ```markdown
   Held in draft while the following changes are implemented:

   - Skip fenced code blocks when the packaging test looks for headings
   - Name the held layer in the stack's readiness report

   <!-- projector-hold v=1 -->
   ```

   Only then does it mark the pull request draft, and it confirms that it
   is one. The order matters. To the review loop, a pull request that turns
   draft on an unchanged head with no hold open is an author who answered
   without pushing, so it re-reviews that head. The comment silences that
   from the moment it lands, and keeps `publish` from marking the pull
   request ready.
2. When another change comes in while the hold is open, run `--add` again
   with it. The command adds its bullet to the same comment rather than
   posting a second one, skips a change already listed, and keeps the marker
   as the last line, so the hold never lapses.
3. When the owner drops a listed change, remove it with `--drop` and the
   text of its bullet. The command refuses a text that matches no bullet,
   and names the bullets the hold lists.

   ```sh
   project review hold <number> --repo <owner>/<repo> --drop "Name the held layer in the stack's readiness report"
   ```

   It deletes a hold comment left with no bullet. Once none is left, the
   hold ends on a head no push changed, which asks the review loop to
   re-review that head, as it should.
4. Build each change, run the full gate, commit, and push.
5. As soon as the push that carries every listed change succeeds, end the
   hold with a bare `--drop`, which deletes every hold comment you have open
   on the pull request, before you resolve threads or report:

   ```sh
   project review hold <number> --repo <owner>/<repo> --drop
   ```

   Never end the hold before that push. A hold ended on the unchanged head
   asks the review loop to re-review it, and a clean verdict would then mark
   the pull request ready while the changes are still local. The command
   does not check this: it trusts you to run it after the push, so keeping
   that order is yours. When a listed change will not be built, drop it by
   name in step 3 rather than ending the hold early.

Each run prints what it did, the comment's id and URL, the changes the hold
lists, and the draft state; `--json` prints the same as one object.

Run `project review hold` as a command of its own, because it is the one
narrow command a session can allow for the hold. Claude Code's auto mode can
refuse a raw `gh api` call that deletes a comment, as a write to an external
system, which leaves a clean pull request stuck in draft. `project init`
adds `Bash(project review hold *)` to the user's Claude Code settings, and
the same allowance to their Codex rules, so the command runs where the raw
call is refused. If the host refuses the command anyway, do not retry it as
a raw `gh api` call. Leave the hold as it is, and ask the user to run
`project init` in a terminal, which adds the rules, or to run the command
themselves.

Projector reads the hold. While the pull request's author has a comment
carrying `<!-- projector-hold v=1 -->` open on it, `project review status`
reports the pull request `held` beside its newest verdict and names the
comment, and `project review publish` keeps it a draft however clean the
newest verdict is. The review loop asks for no re-review of an unchanged
head while the hold is open, and asks for one when the hold ends without a
push. Only a comment by the pull request's author counts, because anyone can
comment. Once you end the hold, the review of the head you pushed signs the
pull request off.

Hold a pull request even when it is already a draft. A draft that is waiting
on its review would be marked ready by a clean verdict, and the comment is
what stops that. Hold only when a change is coming, though: when you defer
every suggestion and the owner asks for nothing, post no comment and leave
the draft state as it is. Your report lists the deferrals.

Never mark the pull request ready yourself, when you end the hold or at any
other time. No mode of `project review hold` marks it ready either. The
review loop's clean verdict on the head you pushed does that.

In a stack, hold the layer that gets the change. The user merges a stack
from the bottom up, so a held layer holds every layer above it too, and
those layers need no comment of their own. Carry the change up through them,
as [Preserve stacked ownership](#preserve-stacked-ownership) describes, and
end the hold once the push that carries every listed change into the
held layer succeeds.

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
3. Push the pull request branch, never its base branch. On a layer with
   layers above it, carry the fix up and push those layers too, as
   [Preserve stacked ownership](#preserve-stacked-ownership) describes.
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
When a hold is open and the push carries the last change it lists, end
the hold with `project review hold <number> --repo <owner>/<repo> --drop`
before you resolve anything, so the review of that push can sign the head
off.

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
describes under "Stack dependent work": by rebasing with `gh stack` where it
works, and by merging each layer into the one above it where it does not.
Either way, run each affected layer's full gate before you push it. Do not
patch parent code inside a child merely to avoid updating the layers above.

The user can merge the whole stack, or its bottom layers, from GitHub at any
moment, so a run that changes a layer with layers above it is not done when
that layer is pushed. It is done once the change is carried up, every layer
above is pushed, and GitHub's comparison shows the stack in sync again, as
that section describes.

After a parent merges, update the layers above it the same way: the rebase
replays them onto the new base and drops a squash-merged parent's commits
from them. Then verify each child points at the intended base and remains
mergeable. Where `gh stack` is unavailable and the parent was squash-merged,
merge the new base into the child even though `mergeable` reads `MERGEABLE`:
until then the child's diff still carries the parent's original commits, and
no conflict event will say so. Never merge any layer; merging remains the
user's checkpoint.

## Report the result

A pull request is clean only when:

- it merges into its base without a conflict,
- `project review status <number>` reports `clean` for its current head,
  which says the newest Projector verdict on it is clean,
- no unresolved threads remain,
- no hold comment is open on it, and in a stack none is open on a layer
  below it, because a held layer holds every layer above it,
- in a stack, it and every layer below it are in sync on GitHub, so the
  stack shows none of them needing a rebase: GitHub's comparison, which
  `../implement/SKILL.md` describes under "Stack dependent work", prints
  `0` for each, whatever a local `gh stack view` reads,
- the current head is the one actually reviewed, and
- the reviewer's own sign-off signal is clear: `reviewDecision` is not
  `CHANGES_REQUESTED` for a cross-author reviewer, and a self-reviewed pull
  request is no longer a draft, which is how that loop signs off.

`project review status <number>` reports the newest Projector verdict on the
current head, whichever model gave it, and the model and review it came from.
It reads the verdicts of every account the repository lets in, as the site
does, so one run covers your own review loop and a teammate's. The newest
verdict wins: a clean verdict after another model's change request signs the
head off, because that model's findings were settled before the clean
verdict could be given, and a change request after a clean verdict takes the
sign-off back. With no verdict on the head it reports `unreviewed`, a head
waiting on its first review. When nothing is outstanding and it reports
anything but `clean`, report the verdict and the model it names, and treat
the head as waiting on a re-review, not on a code change. A Projector review
loop that runs on Codex is one of those models. While the pull request's
author has a hold comment open, it also reports the pull request `held` and
names the comment: a held head waits on the push that carries the hold's
changes, whatever its newest verdict says.

An external reviewer is not one of those conditions. A pending or absent
re-review from Codex, Cursor, or Bugbot does not hold a head back from clean:
report on the review loop's verdict and let theirs arrive whenever it does. A
standing `CHANGES_REQUESTED` from one is a real verdict and does block, exactly
as any other cross-author verdict would.

Report the head SHA, each layer's new head when you carried a change up a
stack, the merge commit if you made one, the fix commits, the declined
findings, the suggestions you implemented, each suggestion you deferred with
its one-line reason, any hold you posted, added to, dropped from, or ended
and the changes it listed, and the validation results. A
stale `CHANGES_REQUESTED`, or a pull request still in draft after all
fixes, is a wait for re-review rather than another code change. Do not
manufacture an empty commit to trigger it.

Never mark a draft pull request ready yourself. A clean review is what clears
a draft, so undrafting by hand sends the work to human reviewers carrying a
sign-off nothing gave it. If you opened it as a draft, you do not undraft it —
not when the reason you drafted it is resolved. That is the case that feels
justified: the red test you drafted around is passing, so the draft reads as
stale state left over from a fixed problem. It is not. It is a standing
request for review that has not been answered yet. A pull request you held
is the same: ending the hold is your part, and the review of the head you
pushed is what marks it ready.

## Never

- Never merge the pull request. Merging its base into its branch is how you
  resolve a conflict; merging the pull request is the user's checkpoint.
- Never push through the reviewer identity or to an unowned branch.
- Never discard or overwrite uncommitted work to switch branches.
- Never force-push except through `gh stack`, which pushes a stack's rebased
  layers with `--force-with-lease`, or to resolve a conflict where the
  repository's instructions call for a rebase, and then use
  `--force-with-lease`.
- Never close the pull request and open a replacement to escape a conflict.
  That discards its review threads; fix the branch in place.
- Never claim a review or clean state without checking live evidence.
- Never invoke an external reviewer unless the user names it, and never
  re-request one after fixing its finding.
