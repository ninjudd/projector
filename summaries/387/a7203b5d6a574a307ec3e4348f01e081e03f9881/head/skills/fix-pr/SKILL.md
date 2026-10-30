---
name: fix-pr
description: Fix one pull request's outstanding review work once. Merge its base when the branch conflicts, verify each finding, fix and push the valid ones, reply to and resolve their threads, decline the rest with evidence, and implement or defer each suggestion, holding the pull request in draft while a change is built. Each push subscribes the session to fix the pull request, so later review work arrives as an event that runs this skill again in one subagent per pull request. Use when the user asks to fix, address, or answer review feedback on a pull request, or to resolve its merge conflicts.
---

# Fix a pull request

Bring one pull request's current head to where a review can sign it off.
Merge its base when the branch conflicts, and answer every outstanding
finding with a pushed fix or a declined reply. Implement or defer each
suggestion a review lists, and hold the pull request in draft while you
build a change no review has read. The fix runs once. Each push subscribes
the session to fix the pull request, so later work on it arrives as an
event, and the session runs this skill again for it, as
[Act on the session's events](#act-on-the-sessions-events) describes.

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

A reviewer may post under a different identity. If the ambient
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
- **Every suggestion you have not decided** among those
  `project review suggestions <number>` lists, as
  [Decide each suggestion](#decide-each-suggestion) describes.
- **A change the owner asks for** in the conversation.
- **Your open hold comment.** The changes it lists are outstanding until a
  push carries them, and the comment itself until you end the hold, as
  [Hold the pull request while changes are built](#hold-the-pull-request-while-changes-are-built)
  describes.

Findings arrive from two kinds of reviewer and both are answered the same way.
A **cross-author** reviewer — a teammate, Codex, Bugbot, or a Projector review
run by someone else — posts real `CHANGES_REQUESTED` verdicts. A
**self-review**, which GitHub allows only to post `COMMENT` reviews on the
operator's own pull requests, records its outcome in the review body's verdict line and in draft
state instead. Read both signals; never treat an unmoved `reviewDecision` as
evidence that nothing is outstanding.

Read review bodies and compare them with the thread set. A Projector review
body carries a verdict line; read it rather than inferring the outcome. Dispose
of a body-only finding in a pull-request-level comment because there is no
thread to reply to or resolve.

The suggestions a Projector review lists in its body are not body-only
findings. They never hold a head back from clean and need no reply, but you
decide each one, and you take them from `project review suggestions` rather
than from the body you read.

Draft state is not itself a finding. It says the head has not been signed
off, so look for the work in the threads and review bodies. A draft with no
outstanding work is waiting on a review, not on a code change. When nothing
is outstanding, say so and stop.

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
as suggestions in its body. A suggestion never holds a head back from clean,
so a review can sign a head off and mark the pull request ready above a list
of them. Each one still gets a decision: you implement it or you defer it.
List them with the command, never by reading review bodies yourself, whose
label and list style vary with the model and the release that wrote them:

```sh
project review suggestions <number> --repo <owner>/<repo>
```

It lists every suggestion in the newest Projector review of the current head
by each model. An incremental review lists none of its own, so the command
also lists those of the full review it builds on. Each block names the file
and line, the head of the review the suggestion comes from, and that review's
URL, with the suggestion below them. With `--json` it gives each as an
object with `url`, `sha`, `path`, `line`, and `text`. It prints nothing when
no review lists a suggestion. Decide every suggestion it lists.

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
   is one. The order matters. To the watch daemon, a pull request that turns
   draft on an unchanged head with no hold open is an author who answered
   without pushing, so it asks the reviewing session to re-review that head.
   The comment silences that from the moment it lands, and keeps `publish`
   from marking the pull request ready.
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
   hold ends on a head no push changed, which asks the reviewing session to
   re-review that head, as it should.
4. Build each change, run the full gate, commit, and push.
5. As soon as the push that carries every listed change succeeds, end the
   hold with a bare `--drop`, which deletes every hold comment you have open
   on the pull request, before you resolve threads or report:

   ```sh
   project review hold <number> --repo <owner>/<repo> --drop
   ```

   Never end the hold before that push. A hold ended on the unchanged head
   asks the reviewing session to re-review it, and a clean verdict would then
   mark the pull request ready while the changes are still local. The command
   does not check this: it trusts you to run it after the push, so keeping
   that order is yours. When a listed change will not be built, drop it by
   name in step 3 rather than ending the hold early.

Each run prints what it did, the comment's id and URL, the changes the hold
lists, and the draft state; `--json` prints the same as one object.

Run `project review hold` as a command of its own, so the rule that allows
it matches, rather than making its calls with `gh api`. Claude Code's auto
mode can refuse a raw `gh api` call that deletes a comment, as a write to an
external system, which leaves a clean pull request stuck in draft.
`project init` adds `Bash(project *)` to the user's Claude Code settings,
which allows every Projector command, and the same allowance to their Codex
rules, so the command runs where the raw call is refused. If the host
refuses the command anyway, do not retry it as a raw `gh api` call. Leave
the hold as it is, and ask the user to run `project init` in a terminal,
which adds the rules, or to run the command themselves.

Projector reads the hold. While the pull request's author has a comment
carrying `<!-- projector-hold v=1 -->` open on it, `project review status`
reports the pull request `held` beside its newest verdict and names the
comment, and `project review publish` keeps it a draft however clean the
newest verdict is. The watch daemon asks for no re-review of an unchanged
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
clean verdict on the head you pushed does that.

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

Decline a finding that asks you to rewrite or annotate a Projector plan that
is already at `status: completed` on the pull request's base. A completed plan
records what was decided and built, as `../review-pr/method.md` § 6 says, so
reply with that reason and leave the plan as it is. When living documentation
describes the same behavior and the change left it stale, the finding holds
for that document: fix it there, and say in the reply that the plan stays as
it is. A plan this pull request marks `completed` is not a record yet, so fix
a finding about the outcome it records as you would any other.

If the correct behavior requires a user decision, ask instead of guessing and
go on with unrelated findings. If a finding recurs after a pushed fix, stop
and report the recurrence before changing it again.

## Commit, reply, push, resolve

Follow the writing guide in `../writing.md` when you write each commit message
and thread reply. For every accepted finding, preserve this order:

1. Commit the validated fix locally.
2. Reply in its review thread under the operator identity, opening the reply
   with `<!-- projector-reply v=1 -->` so a reviewer sharing this login never
   reads it back as a new finding. Name the commit and state what was
   reproduced and changed.
3. Push the pull request branch, never its base branch. On a layer with
   layers above it, carry the fix up and push those layers too, as
   [Preserve stacked ownership](#preserve-stacked-ownership) describes. Then
   subscribe the session to each pull request the push updated, as
   [Subscribe the session to the pull request](#subscribe-the-session-to-the-pull-request)
   describes.
4. Resolve the thread only after the push succeeds.
5. Re-fetch `reviewThreads` and confirm `isResolved: true`.

Resolving says the fix is pushed; it is not the final word on a Projector
finding. The reviewer verifies every one of its findings on the next head,
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

## Subscribe the session to the pull request

Once a push succeeds, subscribe the session to fix each pull request it
updated: this one, and each layer above it that the push carried a change
up through. That holds for every push this skill makes, whether it carries
a fix, a merge of the base, or a stack's rebased layers. The session then
hears about each pull request's later findings, verdicts, conflicts, and
review bodies as events, and runs this skill again for them, as
[Act on the session's events](#act-on-the-sessions-events) describes.

```sh
project watch add <owner>/<repo>#<number>
```

The role defaults to `fix`. The command writes the subscription to the
session's folder under Projector's state directory, and starts the watch
daemon, one process per machine that polls every pull request a session
subscribes to, when it is not running. Running it again for a pull request
the session already fixes is harmless. The command refuses a pull request
the session excludes, as one the user dismissed in Projector's panel. Tell
the user, and run `project watch restore` with the same arguments only when
they ask. Skip the command when
`project config get watch.auto_start --default true` prints `false`, and say
in your report that the setting kept the session from subscribing.

The command finds the session's id in `CLAUDE_CODE_SESSION_ID`, which Claude
Code sets in every command, a subagent's included, so a subagent subscribes
the session it belongs to. On a host that sets no session id, the
conversation the user talks to chooses one, of letters, digits, `.`, `_`,
and `-`, and gives it to each subagent it starts. Every `project watch`
command there takes it as `--session <id>`, or reads it from
`PROJECTOR_SESSION`.

The command's last line says how the session's events arrive:

| Line | Means | Do |
| --- | --- | --- |
| `events: mod` | Projector's mod in Claude Code submits them as a prompt. | Nothing more. |
| `events: stream` | A `project watch stream` that the session runs prints them. | Nothing more. |
| ``events: none — start `project watch stream --session <id>` …`` | Nothing delivers them yet. | Start the command the line names under the host's persistent process or monitor facility, where it runs until stopped. |

Start the stream only from the conversation the user talks to, because a
subagent's processes end when it returns. A subagent that reads `none` says
so in its report, and the conversation that started it starts the stream.

When the user asks the session to stop fixing a pull request, run
`project watch remove <owner>/<repo>#<number>` and stop its subagent. A
subscription also ends by itself when the pull request merges or closes.

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
  request is no longer a draft, which is how a self-review signs off.

`project review status <number>` reports the newest Projector verdict on the
current head, whichever model gave it, and the model and review it came from.
It reads the verdicts of every account the repository lets in, as the site
does, so one run covers your own reviews and a teammate's. The newest
verdict wins: a clean verdict after another model's change request signs the
head off, because that model's findings were settled before the clean
verdict could be given, and a change request after a clean verdict takes the
sign-off back. With no verdict on the head it reports `unreviewed`, a head
waiting on its first review. When nothing is outstanding and it reports
anything but `clean`, report the verdict and the model it names, and treat
the head as waiting on a re-review, not on a code change. A session that
reviews on Codex is one of those models. While the pull request's author
has a hold comment open, it also reports the pull request `held` and names
the comment: a held head waits on the push that carries the hold's changes,
whatever its newest verdict says.

An external reviewer is not one of those conditions. A pending or absent
re-review from Codex, Cursor, or Bugbot does not hold a head back from clean:
report on Projector's verdict and let theirs arrive whenever it does. A
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

## Act on the session's events

A session subscribed to fix a pull request receives its events. Each is one
line, `<KIND> <owner/repo#number> <detail> — <action>`, which Projector's
mod submits as a prompt in Claude Code and `project watch stream` prints on
another host. The watch daemon sends each event to one session, the one that
owns the pull request's `fix` role, and the line ends with what to do, so a
session that loaded no skill can act on it:

```text
CONFLICT acme/app#12 conflicts with main — run fix-pr on acme/app#12 in subagent fix-12
```

This section holds the rules behind that action. The events for the `fix`
role are these:

- **`FINDING`** reports an unresolved inline review thread.
- **`VERDICT`** reports a real `CHANGES_REQUESTED`, including a review whose
  findings exist only in its body.
- **`CONFLICT`** reports a branch that conflicts with its base. It clears
  when a push makes the branch mergeable again.
- **`REVIEW`** reports a review body that has no inline thread, the shape
  every `COMMENT` review posts. A Projector review that lists suggestions is
  work even under a clean verdict, because this skill implements or defers
  each one. The line says how many the review's `projector-suggestions` line
  counts, as `listing 2 suggestions`. A review an earlier release published
  has no such line, so its line names no count, and
  `project review suggestions` still finds its suggestions.
- **`DRAFT`** reports a head that no clean review has signed off. It is not
  work: any work behind it arrives as its own `FINDING`, `VERDICT`,
  `CONFLICT`, or `REVIEW` line, and a pull request that a hold keeps in
  draft has its work with the subagent already. A draft with nothing else
  outstanding waits for a review. When the line says that no session on
  this machine reviews the pull request, tell the user once, and say that
  `review-pr` or `review-repo` in a session would review it.
- **`MOVED`** reports that another session now owns the pull request's
  `fix` role. Stop its subagent at once, because two fixers would push the
  same branch, and tell the user.
- **`MERGED`** and **`CLOSED`** report that the pull request merged or
  closed, as
  `MERGED acme/app#74 into main at 2026-10-23T01:36:44Z (merge commit ea9a364a)`
  or `CLOSED acme/app#80 without merging`. The daemon removes the
  subscription itself. Act as the next section describes.
- **`MISSING`** reports that the pull request does not exist or that the
  operator cannot see it, and that the daemon removed the subscription.
  Tell the user.

`FINDING`, `VERDICT`, `CONFLICT`, and `DRAFT` report work that stands until
it is done, and the daemon announces each again every `watch.renotify`
seconds, 15 minutes by default, while it stands. So a line about work a
subagent already has is a reminder: send it on. A thread the subagent
declined stays unresolved, because declining leaves the decision to the
user, so its reminder needs nothing more until someone answers the reply.

### Give each pull request its own subagent

Where the host can keep a subagent alive and send it later messages, each
pull request the session fixes gets one fixing subagent for as long as it
stays open. The session routes events and records outcomes; the subagent
owns every fix on its pull request. Fix context that carries over is the
point: on a new round the subagent already knows the findings it answered,
the ones it declined and why, and the commits it pushed, so it reads a
reopened thread as the recurrence it is instead of as new work.

- On a pull request's first `FINDING`, `VERDICT`, `CONFLICT`, or `REVIEW`,
  start its subagent under the name the event gives, `fix-<number>`. Give
  it the repository, the pull request, the operator login, the event lines,
  and the path of this file, and have it run this skill for the pull
  request.
- Give a stack one subagent for all its layers, named `fix-stack-<number>`
  for the bottom layer, so the name says it covers more than that one pull
  request. A fix to a lower layer is carried up into every layer above it,
  so two subagents working one stack would push the same branches. Group the
  pull requests the session fixes into stacks by the lookup that
  `../implement/SKILL.md` describes under "Stack dependent work". It reads
  base branches when GitHub records no stack, so a chain opened without
  `gh stack` still gets one subagent.
- Have each subagent work in its own worktree of its pull request's branch,
  so two subagents never share a checkout and none touches the session's.
  Start each subagent with a working directory of its own where the host
  offers one; in Claude Code, pass `isolation: "worktree"` to the Agent
  tool. An isolated subagent's directory is pinned at launch, so entering
  its pull request's worktree moves only that subagent. A subagent started
  without one shares the session's directory, and its `EnterWorktree`
  moves every subagent with it.
- Where the host cannot give a subagent a directory of its own, tell the
  subagent never to change the session's directory: run every Git command
  as `git -C <worktree>` and give every file tool an absolute path. Where
  you cannot be sure it will, run the subagents one at a time.
- Before you hand a branch to its subagent, make sure the session's own
  checkout does not hold that branch, for example with
  `git switch --detach`. Git checks a branch out in only one worktree, so
  the subagent's `git worktree add` of it fails while yours holds it.
- Send every later event for that pull request, or for any layer of its
  stack, to the same subagent as a message; never start a second one for
  it. Each message is another run of this skill for the pull request's
  current head.
- Send a change the owner asks for on a pull request the session fixes to
  its subagent the same way, starting the subagent if the pull request has
  none yet. This skill holds the pull request in draft while it builds the
  change, as
  [Hold the pull request while changes are built](#hold-the-pull-request-while-changes-are-built)
  describes.
- The subagent reports each run back: the head SHA, each layer's new head
  when it carried a change up a stack, the merge commit if it made one, the
  fix commits, the declined findings, the suggestions it implemented, each
  one it deferred with its reason, any hold it posted, added to, dropped
  from, or ended, and the validation results. A question for the user comes
  back to the session to ask, and the subagent goes on with the pull
  request's unrelated work meanwhile.
- On `MERGED` or `CLOSED`, tell the user that the pull request merged or
  closed, and send its subagent no more work for it. Tell the subagent to
  finish, remove its worktrees, and close only when no layer it owns is
  still open. The user merges a stack as a unit, the whole of it or any
  number of layers from the bottom up, so when only the bottom layers
  merge, the `fix-stack-<number>` subagent keeps its name and goes on fixing
  the layers left open. It starts with the update the lowest of them needs
  onto its new base, as
  [Preserve stacked ownership](#preserve-stacked-ownership) describes.
- On `MOVED`, stop the pull request's subagent. A stack's subagent fixes
  every layer, so a `MOVED` for one layer stops it too. Then ask the user
  which session fixes the stack's other layers, because a fix carried up
  from any of them pushes the layer the other session now fixes.
- When a subagent is lost, to a session restart for example, start a
  replacement under the same name. It rebuilds its context from GitHub:
  each open layer's review threads, the replies on them, and its review
  bodies.

The session does not inspect code, so its own context stays small however
many pull requests it fixes. Where the host has no subagents, the session
runs this skill itself for each pull request with work, in the order the
events arrived.

### Report clean state

After each subagent report, check the pull request against the clean
conditions in [Report the result](#report-the-result), and report its state
with the head SHA, fix commits, declined findings, deferred suggestions, and
validation results. A pull request with an open hold comment is not clean,
however clean its review: `project review status` reports it `held`. Say
so, and why: "#12 is held in draft while 2 changes are implemented", naming
the changes the hold lists.

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
- Never let two subagents push to the same branch.
- Never claim a review or clean state without checking live evidence.
- Never invoke an external reviewer unless the user names it, and never
  re-request one after fixing its finding.
