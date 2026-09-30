---
name: review-pr
description: Review a pull request's current head locally by Projector's written review method and publish one labeled review, setting the pull request's draft state to match the verdict. Use when the user asks to review a pull request or a change once.
---

# Review a pull request

Review one pull request's current head, exactly as pushed, and publish one
review of it. The review runs once and starts no watcher; `start-review-loop`
runs this skill for every new head it sees.

## Choose the pull request

Take the pull request from the user: a URL, or a number in the repository
containing the current working directory. When the user names none, use the
open pull request for the checked-out branch:

```sh
gh pr view --json number,url,state,isDraft,headRefOid,baseRefName
```

When that finds no pull request, or finds one that is not open, ask the user
which pull request to review rather than guessing.

## Resolve identity

A review posts as one GitHub identity, the **reviewer**. By default that is
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

A wrong reviewer fails quietly, posting as the wrong account with nothing to
flag it, and under `start-review-loop` it posts review after review that way,
so the source matters here more than anywhere else in this skill.

Confirm whatever you resolve under the token actually used:

```sh
gh auth status
gh api user --jq .login
```

The **operator** is the other role these instructions name: the account whose
checkout the review runs beside, and under `start-review-loop` the author of
the pull requests the loop tracks. By default it is that same authenticated
user, which is why neither role needs configuring. A reviewer override is
what separates them, and every `<operator>` below means this login, never the
reviewer's.

The operator is deliberately not a setting. It is whoever the token says you
are, and a key that disagreed with the token could only mislabel who did the
work.

Hold validated logins literally in every query you run; never use `@me`. Its
value follows whichever token is live, so under a reviewer override it
resolves to the reviewer rather than the operator.

### Two review modes, chosen per pull request

Compare the reviewer against each pull request's author. Nothing configures
this; it follows from who wrote the code.

Single-identity self-review is this skill's intended shape, not a degraded one
to engineer around. Its constraints are the point rather than the cost: a
review of your own work cannot move `reviewDecision`, so it cannot mark your
code approved, and its verdict stays a claim a reader weighs instead of a
state that clears a merge gate. A second account would unlock those states,
which is precisely why adopting one is not a review's call to make.

**Self-review — the reviewer authored the pull request.** GitHub permits only a
`COMMENT` review on your own pull request, refusing both `APPROVE` and
`REQUEST_CHANGES`, so `reviewDecision` never moves. Two conventions of this
skill carry the outcome instead: the **verdict line** in every review body, and
**draft state**, which is the status a reader sees in the pull-request list.

**Cross-author review — someone else wrote it.** Review verdict states work
normally here. Post `REQUEST_CHANGES` when findings are open. When the head is
clean, post a `COMMENT` review saying so — **never `APPROVE`**. An approval is a
person vouching for code, and an agent must not vouch on the reviewer's behalf;
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
gave. Nothing else enters into it — not when a review first saw the pull
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

## Review an exact head

For the head under review, steps 1 to 4 are one command, run from a checkout
of the repository or given one with `--checkout`:

```sh
project review setup <number> --model <model-id> [--loop <id>] [--rereview]
```

It refuses, exiting non-zero with the reason, unless each step holds, and it
takes every SHA from GitHub, never from an argument:

1. The pull request is open. A head from a fork is set up too, fetched from
   `pull/<number>/head`.
2. It fetches that commit into the checkout and creates a scratch worktree at
   it under its state directory, never inside the checkout. Never run a
   reproducer that writes inside the operator's checkout. Never discard,
   restore, or overwrite the operator's dirty or uncommitted work while
   preparing or cleaning up a review.
3. The fetched commit and the scratch worktree's `HEAD` both equal GitHub's
   recorded SHA.
4. Only then it posts, as the reviewer, a concise start comment naming the
   short SHA, carrying the review signature line's `📽️` mark and its model and
   effort segments and a start marker:

   ```
   📽️ **Projector review started** · model `<model-id>` · effort `<effort>` · reviewing `<short-sha>`

   <!-- projector-start v=1 sha=<full-sha> -->
   ```

It prints the SHA, the merge base, the worktree, whether the head is trusted by
the next section's rule, and the start comment's id and `created_at`, and
records them in the review's state file, from which every later command reads
the checkout, the worktree, and the repository. An untrusted head is reviewed
by reading. It posts with a token for the reviewer from `review.username` or
the authenticated user, set for that call alone; `--reviewer` passes explicit
user input. `start-review-loop` passes its loop id as `--loop` to every
command, and `--rereview` marks a re-review of a head the loop already
published a verdict on, the case `RESPONDED` asks for.

The review holds a lock for the pull request and head from `setup` until it is
published, so a second instance for the same head exits without posting. A
refused `setup` releases it. A lock older than a day is stale and cleared;
`project review release <number>` clears one left by a session that stopped,
and only when no review of that pull request is running.

5. Inspect the head by the method in `method.md`, next to this file: state
   the change's intent, sort the changed files, run the passes, follow every
   changed definition to the code that depends on it, and verify each
   candidate finding by the four-step protocol before it becomes a thread.
   The written-rules pass reads the head against `../guidelines.md`, the code
   guidelines every Projector skill shares, before the repository's own rule
   documents. Cover both the new range and the pull-request-wide integration
   diff.
6. Run focused tests and reproductions proportional to risk, when the head
   is trusted as the next section defines. A candidate the protocol cannot
   verify is dropped, never posted.
7. Re-fetch the head before publishing. If it moved, the review in progress is
   of a stale head: run `project review move <number>`, which repeats steps 1
   to 3 for the replacement SHA and edits the start comment as described next
   instead of posting another, and revalidate every prospective finding against
   the new head.

### Run a head's code only when the head is trusted

Running a test, a reproducer, a build, the repository's validation gate, or
any script from the head executes code whoever pushed the head wrote, on this
machine, with the reviewer's GitHub token and credentials in reach. A
malicious test runs as surely as a malicious build step. Reading the code
runs nothing, so a review can always read; it runs the head's code only when
the head is trusted.

Who can push to the head's branch decides who could have put code on it, so
start there:

```sh
gh pr view <number> --repo <owner>/<repo> \
  --json isCrossRepository,author,commits \
  --jq '"\(.isCrossRepository) \(.author.login)", (.commits[].authors[].login)'
```

A head is trusted only when all three hold:

1. **It lives in the base repository.** `isCrossRepository` is `false`, so
   pushing to its branch took write access to this repository. A fork's
   branch is untrusted whoever owns the fork, because the fork's owner and
   its collaborators can push to it with no role here.
2. **The pull request's author is trusted.** A login is trusted when it is
   the operator, or when this command prints `admin` or `write`, which is
   also what it prints for `maintain`:

   ```sh
   gh api repos/<owner>/<repo>/collaborators/<login>/permission --jq .permission
   ```

3. **No commit names an untrusted author.** A commit's author login comes from
   the email its committer wrote, so it can be forged and never grants trust;
   a commit naming a login that fails the test above, or naming none, still
   withholds it, since it says someone outside the repository wrote that code.

A bot, and a login whose lookup fails, are untrusted.

On a trusted head, run the repository's validation gate with `project review
gate <number>` when `.projector.toml` sets `review.gate`: it runs that command
in the scratch worktree, with the worktree and the merge base as its arguments,
and exits with its status. Without `review.gate`, run the checks the
repository's instructions name. On a head `setup` recorded as untrusted, `gate`
refuses without running anything, and the review proceeds by reading.

When the head is untrusted, review it by reading. The four-step protocol in
`method.md` verifies findings without running anything. Before you run
anything from the head, ask the user, naming why the head is not trusted and
the exact command you want to run, and run it only on a yes. A yes covers the head it was given for; a new head asks again unless
the user said the answer covers the pull request. Publish without waiting for
the answer when reading was enough, and say in the review's disclosures that
the head was reviewed without running its code and why.

### One start comment per review

A start comment belongs to the review it opens, not to the SHA it first named,
and it lives only until that review is published. When the head moves before
publication — step 7 catches it, or `start-review-loop` reports `NEW HEAD`
while inspection is still running — do not post a second start comment. Run
`project review move <number>`, from any directory. It creates a worktree for
the new head, rechecks whether the head is trusted, moves the lock to it, and
edits the existing comment in place so it names the new short SHA, says the
head moved from the old one, and says the review is being updated for the new
changes, keeping its `📽️` signature line and setting its marker's `sha=` to the
new full SHA. It re-reads the comment and refuses, exiting non-zero, unless the
edit took; it also refuses when the head has not moved.

Once the review is published and verified, the start comment goes:
`project review publish` deletes it as its last step. The review's signature
line carries how long the review took, so the comment has nothing left to say,
and a pull request that keeps one per review is noise a reader scrolls past.

A start comment on a pull request therefore means a review in progress. A
session resuming mid-review looks for one on GitHub rather than in memory and
edits it instead of posting a second one. The exception is a leftover from a
session that stopped between publishing and deleting: a comment whose
`created_at` is earlier than the `submitted_at` of a Projector review on its
`sha=`. Delete that one. Compare the timestamps rather than testing for the
review's presence, because a re-review of an unmoved head, such as the one
`start-review-loop` runs on `RESPONDED`, has a live start comment that shares
its `sha=` with the verdict that preceded it.

Do not invoke a separate Codex, Cursor, Bugbot, or other reviewer unless the
user explicitly requests that service. This skill performs the local review.

## Label every review and finding

The operator authors the pull request and posts the review, so nothing about
the author line distinguishes a Projector review from a human comment. Two
markers carry that distinction. Both are required, and both are conventions of
this skill — nothing in GitHub enforces them.

**Every review body opens with a signature line and a marker:**

```
📽️ **Projector review** · model `<model-id>` · effort `<effort>` · **<VERDICT>** · took <duration>

<!-- projector-review v=1 verdict=<clean|changes-requested> model=<model-id> effort=<effort> sha=<full-sha> findings=<n> seconds=<n> covered=<read>/<changed> -->
```

State the model **actually running this review** — the model identifier the
host reports for the running session — never a default copied from this file.
A review whose signature misstates what produced it is worse than an unlabeled
one: a reader weighs a finding by what reviewed it.

The effort segment and `effort=` come only from the host's live value, never
from your own estimate: on Claude Code that is `$CLAUDE_EFFORT`, which follows a
mid-session `/effort` change and which `project review` reads itself. When the
host reports no such value, as on Codex or on a model without effort support,
leave the segment and `effort=` out entirely. A marker is read the same with or
without `effort=`.

`<duration>` is how long the review took, measured from the start comment's
`created_at` to the moment the review is published, in minutes and seconds
under an hour and hours and minutes past it — `took 12m 34s`, `took 1h 04m`.
When the head moved during the review, it says how many heads the time covers:
`took 14m 02s over 2 heads`. The marker's `seconds=` carries the same span as a
whole number. `project review publish` writes the signature line and marker
itself, from the review's state and one reading of the clock, so the duration,
`seconds=`, and `sha=` are never typed and never disagree.

The visible verdict word is `CLEAN` or `CHANGES REQUESTED`, matching the
marker's `verdict=`. `clean` means no finding thread on the pull request is
open — a thread outlives the head it was filed on, so findings from earlier
heads count until resolved; `changes-requested` means at least one is open.
Print the census the verdict rests on — `6 finding threads: 4 resolved, 2
open` — counting the threads this review opens among the open, so a
changes-requested review on a fresh head never prints `0 open` above its own
findings. `clean` requires that last number to be zero, and printing it
lets a reader see the verdict was earned.

`findings=` is the number of P1 and P2 threads this review opens. P3 items
are listed in the body rather than posted as threads, and are not counted.
`covered=` is how many of the changed files a pass or the reviewer read, over
how many files the head changed; the body's coverage line prints the same two
numbers.

The body follows the order `method.md` § 7 gives: the signature line and
marker, the intent paragraph, the census, the coverage line, any disclosures,
a `Suggestions` list of P3 items when there are any, and on a clean head what
was checked.

**Every inline finding opens with a marker:**

```
<!-- projector-finding v=1 priority=<P1|P2> sha=<full-sha> -->
```

Priority says what happens if nobody acts, and only the first two are
threads:

- **P1.** A defect the change introduces or worsens, with concrete impact on
  users, data, money, availability, or security. A thread; blocks.
- **P2.** A defect the change introduces with bounded impact, or a violation
  of a rule the repository wrote down or of a section of `../guidelines.md`.
  A thread; blocks.
- **P3.** A change the code is correct without: a simplification, dead code,
  a gap between a written rule and practice, a defect already present at the
  base. One line in the body's `Suggestions` list; never a thread, never
  blocks.

The visible text of a thread's first comment has three parts, the shape
`method.md` § 7 shows with an example:

```
**P1 · <what goes wrong and what it costs, in about a dozen words>**

<one or two sentences of concrete behavior that name the symbol>

**Fix:** <one sentence>
```

### Verify earlier findings

A finding is outstanding until you verify it is addressed. Whether its thread
is marked resolved is a hint, not the answer: an author may resolve a thread
before the fix works, and another may fix the code and never resolve it. So
before choosing a verdict, verify every Projector finding thread on the pull
request, resolved or not, against this head, and settle each one yourself.
You are the only party whose resolution is final. This command lists them,
reading every page so that a pull request with more than a hundred threads is
read to the end; it keys on the finding marker, never on a login:

```sh
project review census <number> --json
```

Each thread carries its id, whether it is resolved, its `path` and `line`,
whether it is outdated, and its comments, the finding first and then the
replies. Without `--json` it prints the count the review body states, such as
`6 finding threads: 4 resolved, 2 open`, and each open thread's `path:line`.

An `outdated` thread is one whose line the fix moved: GitHub nulls `line` and
keeps `originalLine`. Outdated says the code changed, not that the finding was
addressed, so verify it like any other.

Read each thread's replies with the finding, verify by `method.md` § 5 against
this head, and settle it in one of these ways. Every reply you post opens with
`<!-- projector-verify v=1 result=<result> sha=<full-sha> -->`, so neither loop
reads it back as a new finding or as the author answering:

- **Fixed:** the head addresses the finding. Resolve an open thread with
  `resolveReviewThread`, replying `result=fixed` with what at this head
  settles it. A thread already resolved and fixed needs nothing more.
- **Accepted:** the author declined in a reply and the reasoning holds, or the
  finding was wrong. Resolve it, replying `result=accepted` or
  `result=withdrawn` with the guard or behavior that settles it.
- **Kept:** the author declined and the reasoning does not hold. Leave the
  thread open and reply `result=kept` once, saying what still fails and why.
  Do not repeat the reply on a later head unless something changed; an open
  disagreement is the user's merge decision.
- **Reopened:** the thread is resolved but the finding is not addressed at
  this head. Unresolve it with `unresolveReviewThread`, and reply
  `result=reopened` with what still fails.
- **Still open:** open, not fixed, and not answered. Leave it; the finding
  already says what to do.

Settle only threads that carry the finding marker. Another reviewer's thread,
a person's or another tool's, is theirs to resolve, never yours. The fix loop
answers in a thread with `<!-- projector-reply v=1 -->`; that reply is the
author speaking, and it is what you weigh when a finding is declined.

## Publish one review

P1 and P2 findings always go out as inline threads in **one** review, each
carrying the finding marker, and every review body carries the signature and
verdict line. P3 items are not threads: list them in the body under a
`Suggestions` heading, as `method.md` § 7 describes. Do not drip-feed findings
or use ordinary issue comments for them. What differs between the modes is
only the review state and what marks the outcome.

GitHub anchors an inline comment only to a file within the first 3,000 files
of the diff, taken in path order; a thread on any later file fails with `422
Path could not be resolved`, however correct the path and line. On a pull
request that large, probe each anchor before the real submission: create a
pending review holding the one comment, then delete it. When a file falls
outside the window, anchor the finding to an in-window file that exercises the
same code — the CI step, test, or README line that runs it — and name the real
`path:line` in the first sentence, so a reader lands on the code rather than on
the anchor.

Publish with `project review publish`, from any directory:

```sh
project review publish <number> --verdict <clean|changes-requested> \
  --body <file> [--threads <file>] --covered <read>/<changed> [--loop <id>]
```

The body file is the review below its signature line: the intent paragraph,
the census, the coverage line, disclosures, `Suggestions`, and what was
checked. Put `{census}` where the census goes; `publish` fills it with the
count the verdict rests on, and also fills `{took}`, `{seconds}`, `{sha}`, and
`{short_sha}` where you use them. It fills them only in prose: code quoted in
backticks or a fenced block, and any other text in braces, goes out exactly as
written. The threads file is a JSON list of findings,
each `{"path", "line", "priority", "body"}`, where `body` is the visible text
below — `publish` adds the finding marker with the head's SHA. It refuses,
exiting 1 with what to fix and keeping the lock so you can correct the input
and run it again, when:

- the pull request is closed or its head moved since `setup`;
- a finding's line is outside the pull request's diff hunks, it has no line,
  its text does not open with its priority header or lacks a `**Fix:**` line,
  or it opens a `<!-- projector-… -->` marker comment of its own;
- the body is missing or empty, opens with its own signature line or carries
  a marker comment, or has no `{census}` outside code;
- the verdict disagrees with the census: `clean` with a finding open or
  being posted, or `changes-requested` with none;
- the collision check below trips.

The collision check lists every verdict the reviewer has already posted on
this exact SHA, reading every page, since each fix-loop thread reply adds a
review object of its own. A verdict this run did not publish means the same
account already reviewed this commit, whether an earlier run or another agent
reviewing it now. Hold the review and ask the user whether to submit, rather
than either submitting or standing down. Two verdicts from one account on one
commit read as a single thorough review, so the overlap goes unnoticed unless
this check catches it. Standing down silently is no better: a second review is
sometimes requested deliberately, and a head both reviews walk away from gets
no verdict at all. If the user says to submit, run `publish` again with
`--second-verdict <earlier-review-id>` and name that review in the body, so a
reader sees the second verdict was deliberate.

When `start-review-loop` runs this skill, it passes `--loop <id>`, and the
loop's record of the reviews it published decides instead, as that skill
describes: a verdict another loop posted trips the check, and a second verdict
on a head this loop already published needs `setup --rereview`, the re-review
`RESPONDED` asks for.

A head is *clean* only when, after you have settled every earlier finding as
above, no Projector finding thread is open **and this review posts no P1 or P2
thread**. A `Suggestions` list in the body does not count against it: a review
whose only output is that list is clean. Settle the earlier findings now,
before choosing a branch, and re-run the query to confirm none is open. Both
tests are one-way: a finding you kept or reopened makes the head not clean
whatever else inspection found, and a new thread makes it not clean whatever
the earlier findings came to. Because you resolve what you verify, a clean
head needs no second pass to notice that someone clicked resolve, and an
author who fixes a finding without resolving it is not held up. Under
`start-review-loop` the watcher asks for a re-review on an unmoved head when
the author answers: `RESPONDED` fires when every thread is resolved, or when
the newest comment on an open finding thread is the author's.

`publish` chooses the review state and draft transition from the mode:

**Threads open, self-review:** a `COMMENT` review, verdict
`changes-requested`, then the pull request returns to draft with `gh pr ready
<number> --undo`.

**Threads open, cross-author:** a `REQUEST_CHANGES` review, verdict
`changes-requested`. Draft state is left alone.

**Clean head, self-review:** a `COMMENT` review, verdict `clean`, then the
pull request is marked ready with `gh pr ready <number>`. That transition is
the sign-off a reader sees in the pull-request list.

**Clean head, cross-author:** a `COMMENT` review, verdict `clean`; say
plainly in the body that the head looks clean and that a human approval is
what remains. It posts a real `APPROVE` only where `review.allow_approve` is
`true`.

Never downgrade a `REQUEST_CHANGES` you could post to a `COMMENT` to work
around a permission failure, and never read GitHub's refusal of a self-review
verdict as one.

Claude Code's auto mode refuses a clean self-review's publish as
self-approval unless the user's Claude Code settings, or the repository's,
allow `Bash(project review publish *)`, the rule `project init` adds to the
user's `~/.claude/settings.json`. Run `publish` as a command of its own so
the rule matches it. If the host refuses it anyway, do not retry it in
another form: keep the lock and the body, and ask the user to run
`project init` in a terminal, which adds the rule to their own settings, or
to run the publish command themselves. `init` adds the rule only for a
person at a terminal, so do not run it to add the rule yourself.

After submitting, `publish` sets draft state for a self-review, re-reads the
review it posted and the pull request's `isDraft`, adds the review id to the
loop's record, deletes the start comment, and releases the lock, in that
order, so the pull request is never left with neither a start comment nor its
verdict's draft state. It records the review as submitted before any of those
steps, so a run that fails partway exits non-zero and running it again
finishes that review instead of posting a second one.

Report the review as done only after `publish` exits 0. Report the SHA, the
verdict, the review id, how many threads it opened, and the summary's URL when
the next section publishes one. Before a `clean` verdict, settle every
earlier finding first; `publish` refuses one while a finding is open. Never
resolve another reviewer's thread, resolve a finding you have not verified at
this head, claim a newer SHA was reviewed, or merge.

Once the summary below has run or been skipped, remove the scratch worktree,
which a summary reads for context. A `start-review-loop` subagent may instead
keep it for the pull request's next head and remove it when the pull request
closes.

## Summarize a large pull request

After the review is published, give a large pull request a summary its human
reviewers can read, with the `summarize-pr` skill in
`../summarize-pr/SKILL.md`. A summary reads the whole diff and writes a
page of prose, so it costs about as much as the review again: summarize only
when all three hold.

1. **Summaries are on.** `project config get review.summarize --default true`
   prints anything but `false`.
2. **The pull request is large.** Its added and deleted lines together reach
   `review.summarize_min_lines`, 400 unless configuration sets another
   number:

   ```sh
   gh pr view <number> --repo <owner>/<repo> --json additions,deletions \
     --jq '.additions + .deletions'
   project config get review.summarize_min_lines --default 400
   ```

3. **The repository hosts summaries.** `project site status --repo
   <owner>/<repo> --pr <number>` exits 0 and prints the summary's URL. Without
   a Projector site there is nowhere to publish unattended, so skip the
   summary rather than publishing an Artifact nobody asked for.

Then follow `summarize-pr` for this head, with two differences from a
summary a person asks for:

- **Update rather than start over.** When the summaries ref already holds a
  spec for an earlier head of this pull request, start from that spec and
  update it as the skill's "Update the page when the pull request moves"
  section describes, so a fix-cycle head costs a revision, not a rewrite.
- **Carry the review into it.** Every finding thread still open after this
  review, from the query in § Verify earlier findings, becomes a `flag` check on its
  file at its `line`, saying what the finding says, so a reader sees what
  the reviewer flagged under the code it concerns. Drop a `flag` check whose
  finding thread has since been resolved.

Publish it with `project summary publish`, after the review. It then
comments a link to the summary on the pull request, so a reader on GitHub
finds it: one comment per pull request, which a later head's summary
updates in place. Do not post the link yourself. A summary that fails to
build or publish never changes the review's verdict: say in your report
that the summary failed and why, and leave the review as it was.

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
