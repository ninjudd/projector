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

### Draft means the head is not signed off

On your own pull request draft state *is* the sign-off: draft means the head
is not signed off, ready means every model reviewing it found it clean.
`project review publish` sets it after it submits, by the sign-off rule in
[Review as one model among several](#review-as-one-model-among-several): it
returns the pull request to draft when any model's verdict on the head
requests changes, marks it ready when every one is clean, and leaves it as it
is while a model is still reviewing the head or a finding thread is open.
With one model reviewing, that is the old rule: a review with findings marks
it draft, a clean review marks it ready. Nothing else enters into it — not
when a review first saw the pull request, not who last changed the state, not
whether it was ever a draft. Never set it yourself with `gh pr ready`.

Draft carries the outcome there because GitHub refuses `APPROVE` and
`REQUEST_CHANGES` on your own pull request, so `reviewDecision` never moves
and draft state is the only list-visible channel left. On someone else's
`reviewDecision` does move, so draft state is not needed as a channel and is
not yours to move: post `REQUEST_CHANGES` and leave their state alone.

So **open your own pull requests as drafts by default**, every layer of a
stack included. The draft says the head has not been signed off; the ready
transition is the sign-off a reader sees in the pull-request list.

### Review as one model among several

Review loops can run on several models at once, such as one in Claude Code and
one in Codex, each posting as the same reviewer account, to give a pull
request separate opinions. Projector tells them apart by the model, not the
account. A reviewer is the model ID you pass as `--model`, compared
lowercased and without a trailing bracketed variant such as `[1m]`, within
the reviewer's account. The effort and the Projector version are not part of
it.

- **The same model twice**, on one machine or two, is one reviewer running
  twice, which is usually an accident such as a session left running.
  `setup` refuses on the lock, or on the other session's live start comment,
  and `publish` refuses on a verdict that model already posted on the head.
- **Another model** is a second opinion. Its review keeps its own state, lock,
  and scratch worktree, its start comment never stops your `setup`, and its
  verdict never trips your collision check.
- **A model upgraded in the middle of a pull request**, such as a loop
  restarted on a newer version, is a new reviewer. The old ID stops, and a
  model still reviewing adopts its findings, as
  [Who re-checks a finding](#who-re-checks-a-finding) describes.
- **Each model re-checks its own findings**, and adopts those of a model that
  has stopped, by the same section.

One rule over GitHub's state decides the sign-off, and every `publish`
applies it after it submits, so the last model to finish sets the draft
state. It reads the reviewer account's verdicts and start comments and every
Projector finding thread, from any account. A start comment is live until
its model posts a verdict on its head, or until a day has passed since it was
last edited, and a model is reviewing the head while its start comment on the
head is live. A model is recorded as stopped while an adoption reply naming
it is newer than anything it has posted, and its verdicts do not count,
because the model that adopted its findings speaks for them. Each other
model's newest verdict on the head counts, and the head is:

- **`changes-requested`** when any model's newest verdict on it requests
  changes. A self-review returns to draft.
- **`waiting`** when none does, and a model is reviewing the head or a
  Projector finding thread is open. The draft state stays as it is.
- **`clean`** when at least one model has a verdict on the head, every
  model's newest verdict on it is clean, no model is reviewing it, and no
  finding thread is open. A self-review is marked ready.
- **`unreviewed`** when no model has a verdict on the head.

A loop that has not noticed a push yet has posted nothing for the new head.
So before `publish` marks a draft ready, it waits for each peer: another
model, not recorded as stopped, with a verdict on an earlier head, or a live
start comment on another head, and nothing on this one. It waits until
`review.peer_wait` seconds, 180 by default, after this review began on the
head, re-reading every 15 seconds. A peer's start comment makes the head
`waiting`, a peer's verdict joins the rule, and a peer that posts nothing by
then is absent. A review that already took longer does not wait, and neither
does one that leaves a pull request already ready, or one on a head that
moves meanwhile. Run
`publish`, and on another author's pull request any `publish` that may
approve, with a timeout of at least `review.peer_wait` plus two minutes.

`project review status <number>` prints the rule's result for the current
head, each model's part in it, and the open finding threads, as
`docs/cli.md` describes; `fix-pr` reads it to tell whether a head is signed
off.

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
   short SHA, carrying the review signature line's `📽️` mark, its version,
   model, and effort segments, and a start marker:

   ```
   📽️ **Projector review started** · `<version>` · model `<model-id>` · effort `<effort>` · reviewing `<short-sha>`

   <!-- projector-start v=1 sha=<full-sha> model=<model-id> -->
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

`--model` is the model ID the host reports for this session, never a default
copied from this file. It names the start comment and keys the review's
state, so each model reviewing the pull request keeps its own state, lock,
and scratch worktree, as
[Review as one model among several](#review-as-one-model-among-several)
describes. Each later command, `move`, `census`, `interdiff`, `gate`,
`adopt`, `publish`, and `release`, takes `--model` too, and finds the review
by the pull request and the model. It refuses, naming the models, when more
than one model has a review of the pull request set up on this machine and
`--model` does not pick one. `census` also runs before `setup`, as a loop runs
it on `RESPONDED`, reading as the model `--model` names.

The review holds a lock for the pull request, head, and model from `setup`
until it is published, so a second instance of the same model for the same
head exits without posting. A refused `setup` releases it. A lock older than
a day is stale and cleared; `project review release <number>` clears one left
by a session that stopped, and only when no review of that pull request by
that model is running. `setup` also refuses while a live start comment shows
the same model reviewing the pull request in another session, on this
machine or another, and names the comment; delete it only once you know that
session has stopped.

Under `start-review-loop`, run `project review interdiff <number>` next. A head
that changed little since the loop's last full review can get an incremental
review in place of steps 5 to 7, as
[Review a minimal push incrementally](#review-a-minimal-push-incrementally)
describes. Every other head continues here.

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
`created_at` is earlier than the `submitted_at` of a Projector review by the
same model on its `sha=`. Delete that one. Compare the timestamps rather than
testing for the review's presence, because a re-review of an unmoved head,
such as the one `start-review-loop` runs on `RESPONDED`, has a live start
comment that shares its `sha=` with the verdict that preceded it. Compare the
model too: another model's start comment belongs to that model's review,
which is still running. The marker's `model=` names the model, and a start
comment from an earlier release, whose marker names none, is read by the
model its first line names. `setup` deletes a start comment this machine's
state recorded for the model and never published, because the new review
replaces it.

Do not invoke a separate Codex, Cursor, Bugbot, or other reviewer unless the
user explicitly requests that service. This skill performs the local review.

## Review a minimal push incrementally

An incremental review reads only the change since a review loop's last full
review of the pull request, and publishes a short clean verdict on the new
head that names the full review it builds on. It is a verdict like any other,
so draft state, the site's header, and `fix-pr` read it as they read a full
review. Only a loop's own full review can carry one, so a review run by hand
is always full.

After `setup`, run:

```sh
project review interdiff <number> [--json]
```

It finds this loop's last full review of the pull request and measures the
pull request's own change since that review's head, whether the author pushed
a commit, merged the base branch, rebased, or rewrote a commit. It leaves out
what the base brought in. Where the author resolved a conflict, the patch
removes the conflict region, with the base's side, the original lines, and the
reviewed head's side between diff3 markers, and adds the resolution. It prints
the earlier full review, how the head moved, the counts, any conflicted paths,
and the patch. It exits 0 only when the rules that need no judgment all hold:

- `review.incremental` is not `false`, and a loop runs the review.
- The head is trusted.
- The loop's record holds a clean full review of the pull request, and the
  newest verdict in it is clean and names another head.
- That full review is on GitHub, and its marker names the Projector version,
  model, and effort this run would write.
- No finding thread is open that this model re-checks, or may adopt.
- The change can be measured, and it touches no binary file, symlink, or
  submodule.
- When the merge base moved since the full review, a `project review gate`
  run on this head has exited 0.

The gate rule is checked last. When it is the only rule that fails, the
refusal ends `(needs_gate)`, and `--json` sets `needs_gate`, so you read the
change before you spend the gate on it. Any other non-zero exit means a full
review: continue at step 5 above, on the same setup. That includes the exit
from a `project` too old to know `interdiff`.

On exit 0, or when only the gate remains, read every line of the patch, and
the code around each hunk at the head where you need context. Then ask one
question: would the full method, with its gate, focused tests, and reading
outward from each changed definition, catch something this read cannot? The
review is incremental only when the answer is no. Run the full review when the
answer is yes, when a hunk reworks code an earlier finding thread was about,
or when you are unsure. A needless full review costs a minute or two. A wrong
incremental review signs off a change that no pass read. A problem the read
finds goes to the full review, which verifies it and files it as a thread.

These kinds of change usually qualify:

- **Presentation.** A value in a stylesheet that changes only how something
  looks: spacing, a size, a color, a border, or a font. A change to which
  elements a rule selects, to whether content shows, or to whether it can be
  clicked is not presentation.
- **Prose.** Comments, docstrings, and documentation text. A command, a code
  sample, a link target, or a configuration value in documentation is not
  prose, because a reader runs or follows it. Text an agent follows as
  instructions, such as a skill's `SKILL.md`, `AGENTS.md`, or `CLAUDE.md`, is
  not prose either, because it changes what the agent does. A string inside
  code, such as a label or an error message, is code, because a test or a
  caller can match it.
- **Formatting.** Whitespace and line wrapping that the file's language
  ignores. Indentation in Python or YAML is not formatting.

Prose qualifies only when a full review would pass it, so read it the two ways
a full review does. Read it as `method.md` § 2 reads documentation: against
the code it describes at the head, for accuracy, broken references, and
contradictions. Read each added or changed comment and docstring against
`../guidelines.md` § 1 as well. A comment that misstates the code is a
correctness finding there, and one that narrates the code beneath it, excuses
complexity, or asserts that a decision is correct is a written-rules finding.

How the head moved, the `kind` that `interdiff` prints, also guides the
decision:

- **A pushed commit (`push`), a clean merge (`merge`), or a clean rebase
  (`rebase`)** is the usual incremental case. The patch holds only the pull
  request's own change.
- **A rewrite in place (`rewrite`)**, such as an amended or squashed commit,
  reads like a pushed commit. `unchanged_commits` says whether any reviewed
  commit's content changed or only its message did.
- **A retarget (`retarget`)** moved the merge base without a merge, as when a
  stacked pull request is retargeted onto the default branch after the one
  beneath it merges. The patch then holds whatever the pull request's diff
  gained, such as the merged parent's commits that GitHub now shows in it.
  Read those like any other hunk.
- **A resolved conflict**, listed under `conflicts`, usually needs a full
  review, because the resolution combines two changes that no review read
  together. Review it incrementally only when every resolved hunk is trivial,
  such as two edits to one stylesheet value, both sides adding independent
  lines, or a version string.

When the read decides for an incremental review and the gate is what remains,
run `project review gate <number>`, and go on only when it exits 0. Where
`review.gate` is unset, `gate` refuses, and the head gets a full review.

Then write the body: a few sentences on what changed and what you checked
about it, at most 600 characters once its placeholders are filled. Leave out
the intent paragraph, the census, the coverage line, disclosures,
`Suggestions`, and the list of checks, because the full review already says
them. Publish it:

```sh
project review publish <number> --incremental --body <file>
```

`publish` checks the lock and the head as for any review, applies the rules
again to the live state, and runs the collision check. It writes the
signature line, both markers, and a lead that links the full review and states
the size of the change, how the head moved, and any conflict the author
resolved, so none of them is typed. It posts a `COMMENT`, even where
`review.allow_approve` is `true`, because an incremental review vouches only
that the change since a reviewed head needed nothing more. On a self-review it
then applies the sign-off rule, as any clean verdict does, and on another
author's pull request it ends the body with "A human approval is what
remains." It refuses, exiting 1 and keeping the lock so you can run the full
review on the same setup, when:

- a rule fails, such as a thread opened since `interdiff` ran;
- the head moved since `setup`, in which case run
  `project review move <number>` and start again at `interdiff`;
- the body is empty, longer than 600 characters once filled, or holds a
  signature line, a marker comment, or `{census}`;
- you pass `--threads`, `--covered`, `--second-verdict`, or
  `--verdict changes-requested`.

Then summarize the new head as
[Summarize the pull request](#summarize-the-pull-request) says, and report as
[Publish one review](#publish-one-review) says, naming the head of the full
review the incremental review builds on.

A full review of a head that `interdiff` measured may read its new range from
the patch `interdiff` printed: the change since the last full review.

## Label every review and finding

The operator authors the pull request and posts the review, so nothing about
the author line distinguishes a Projector review from a human comment. Two
markers carry that distinction. Both are required, and both are conventions of
this skill — nothing in GitHub enforces them.

**Every review body opens with a signature line and a marker:**

```
📽️ **Projector review** · `<version>` · model `<model-id>` · effort `<effort>` · **<VERDICT>** · took <duration>

<!-- projector-review v=1 verdict=<clean|changes-requested> projector=<version> model=<model-id> effort=<effort> sha=<full-sha> findings=<n> seconds=<n> covered=<read>/<changed> -->
```

State the model **actually running this review** — the model identifier the
host reports for the running session — never a default copied from this file.
A review whose signature misstates what produced it is worse than an unlabeled
one: a reader weighs a finding by what reviewed it.

The version segment and `projector=` name the version of the `project`
command that wrote them, as `project --version` reports it, so a reader can
tell which release of this method produced a review. `project review` reads
the version itself; a command run from a checkout that was never installed
reports `unknown`.

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
marker's `verdict=`. `clean` means no finding thread your model re-checks
is open — a thread outlives the head it was filed on, so findings from
earlier heads count until resolved; `changes-requested` means at least one is
open. Another model's open finding does not change your verdict, and it
holds the sign-off until that model settles it.
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

**An incremental review adds one line.** Its signature line carries
`incremental from <short-sha>` between the verdict word and the duration, and
the line directly after its marker names the full review it builds on:

```
<!-- projector-incremental v=1 from=<full-sha> kind=<push|merge|retarget|rebase|rewrite> insertions=<n> deletions=<n> files=<n> conflicts=<n> -->
```

`from=` is the head of that full review, `kind=` is how the head moved since
it, the counts are the change's size, and `conflicts=` is how many files the
author resolved a conflict in. Its review marker keeps the grammar above, with
`verdict=clean`, `findings=0`, and `covered=<n>/<n>` for the files the change
touches, so everything that reads a verdict reads it. A lead linking the full
review follows the two lines, and then the reviewer's short body, as
[Review a minimal push incrementally](#review-a-minimal-push-incrementally)
describes. `publish --incremental` writes all of it except that body.

**Every inline finding opens with a marker:**

```
<!-- projector-finding v=1 priority=<P1|P2> sha=<full-sha> model=<model-id> -->
```

`model=` names the model that opened the finding. `publish` writes the whole
marker, so the threads file leaves it out.

Priority says what happens if nobody acts, and only the first two are
threads:

- **P1.** A defect the change introduces or worsens, with concrete impact on
  users, data, money, availability, or security. A thread; blocks.
- **P2.** A defect the change introduces or worsens, with bounded impact, or
  a violation of a rule the repository wrote down or of a section of
  `../guidelines.md`. A thread; blocks.
- **P3.** A change the code is correct without: a simplification, dead code,
  a gap between a written rule and practice, a defect already present at the
  base that the change does not worsen. One line in the body's `Suggestions`
  list; never a thread, never blocks.

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
before choosing a verdict, verify every Projector finding thread your model
re-checks, resolved or not, against this head, and settle each one yourself.
For those threads you are the only party whose resolution is final. This
command lists every thread, reading every page so that a pull request with
more than a hundred threads is read to the end; it keys on the finding
marker, never on a login:

```sh
project review census <number> --json [--model <model-id>]
```

Each thread carries its id, whether it is resolved, its `path` and `line`,
whether it is outdated, and its comments, the finding first and then the
replies, each with its `login` and `at`. It also says who re-checks it, as
[Who re-checks a finding](#who-re-checks-a-finding) describes: `model`,
`verifier`, `mine`, `adopt`, `adopt_at`, and `answered`. Without `--json` it
prints the count the review body states, such as
`6 finding threads: 4 resolved, 2 open`, and each open thread's `path:line`.
The count covers the threads marked `mine`, and when other models have
threads it adds them on a second clause:
`4 finding threads from claude-opus-5-5: 4 resolved, 0 open. Other models: 2 threads, 1 open`.

An `outdated` thread is one whose line the fix moved: GitHub nulls `line` and
keeps `originalLine`. Outdated says the code changed, not that the finding was
addressed, so verify it like any other.

Read each thread's replies with the finding, verify by `method.md` § 5 against
this head, and settle it in one of these ways. Every reply you post opens with
`<!-- projector-verify v=1 result=<result> sha=<full-sha> model=<model-id> -->`,
naming the model running this review, so neither loop reads it back as a new
finding or as the author answering:

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
a person's or another tool's, is theirs to resolve, never yours. `fix-pr`
answers in a thread with `<!-- projector-reply v=1 -->`; that reply is the
author speaking, and it is what you weigh when a finding is declined.

### Who re-checks a finding

When several models review the pull request, each re-checks its own
findings, so that two models still reviewing never settle each other's, and a
finding whose model has stopped passes to a model that is still reviewing.
Census marks each thread:

- **`mine: true`**: your model re-checks it. That is a finding your model
  opened, one it adopted, or one that names no model, which an earlier
  release opened and every model re-checks. Verify and settle it as above.
- **`adopt: "now"`**: the model that opened it, and any that adopted it, has
  stopped reviewing the pull request. Re-check it as you would your own, and
  settle it with `project review adopt`, below, even when it is resolved and
  its fix holds, with `--result fixed`. The adoption reply records on GitHub
  that the model stopped, so no later review waits for it and its verdicts
  stop counting toward the sign-off.
- **`adopt: "wait"`**: its verifier owes a review, and its `review.peer_wait`
  window closes at `adopt_at`. Leave it until then.
- **`adopt: "no"`** and not `mine`: another model still reviewing re-checks
  it. Leave it to that model, and never reply on it or resolve it.

A model has stopped when it owes the pull request a review, has posted
nothing since it began to owe it, and `review.peer_wait` seconds have passed.
Posting means a start comment, a verdict, or a verify reply naming the model.
A model with a live start comment has not stopped. It owes a review when it
has a verdict on an earlier head and none on this one, when its newest
verdict on this head requests changes and none of its threads is open, and
when an author's reply on a thread it re-checks is newer than anything it
has posted. The verifier of a thread is the model that opened it, under the
account that opened it, unless it has stopped; otherwise the first model
whose adoption reply is on the thread and has not stopped; otherwise no one
yet. So the first adoption reply wins, and a model that comes back, by
posting a start comment or a verdict, gets its findings back. A Projector
finding from another account follows the same rules, read from that
account's own posts.

Adopt a thread with one command, which holds the wait, the claim, and the
check that the claim won in one place:

```sh
project review adopt <number> <thread-id> --result <fixed|accepted|withdrawn|kept|reopened> --body <file> [--model <model-id>]
```

The body file is the rest of the reply, as for any verify reply. `adopt`
refuses a thread your model re-checks already, one that names no model, and
one whose verifier still reviews. While the verifier's window is open, it
waits, reading again every 15 seconds, and refuses if the verifier posts in
the meantime. It then posts the reply, opening with
`<!-- projector-verify v=1 result=<result> sha=<full-sha> model=<model-id> adopted-from=<model-id> -->`
and a line naming both models, and reads the thread again. When another
model that has not stopped adopted it first, it deletes its reply and refuses;
leave the thread to that model. Otherwise it applies the result: it resolves
the thread for `fixed`, `accepted`, or `withdrawn`, unresolves it for
`reopened`, and leaves it as it is for `kept`. Every thread your model adopts
counts as `mine` from then on.

`publish` counts the threads marked `mine` for the verdict, and refuses
either verdict while a thread marked `adopt: "now"` is open, naming it, so
settle those first. Re-run census just before you choose the verdict, because
a window that was open when the review began has usually closed by then. Run
`publish`, `adopt`, and `census --wait` with a timeout of at least
`review.peer_wait` plus two minutes.

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
  --body <file> [--threads <file>] --covered <read>/<changed> [--loop <id>] [--model <model-id>]
```

The body file is the review below its signature line: the intent paragraph,
the census, the coverage line, disclosures, `Suggestions`, and what was
checked. Put `{census}` where the census goes; `publish` fills it with the
count the verdict rests on, and also fills `{took}`, `{seconds}`, `{sha}`, and
`{short_sha}` where you use them. It fills them in prose and in a backtick
span that holds one placeholder alone, so `` `{short_sha}` `` shows the SHA in
code font. Longer code in backticks, a fenced block, and any other text in
braces go out exactly as written. The threads file is a JSON list of findings,
each `{"path", "line", "priority", "body"}`, where `body` is the visible text
below — `publish` adds the finding marker with the head's SHA. It refuses,
exiting 1 with what to fix and keeping the lock so you can correct the input
and run it again, when:

- the pull request is closed or its head moved since `setup`;
- a finding's line is outside the pull request's diff hunks, it has no line,
  its text does not open with its priority header or lacks a `**Fix:**` line,
  or it opens a `<!-- projector-… -->` marker comment of its own;
- the body is missing or empty, opens with its own signature line or carries
  a marker comment, or has no `{census}` outside quoted code;
- the verdict disagrees with the census: `clean` with a finding open or
  being posted, or `changes-requested` with none;
- the collision check below trips.

The collision check lists every verdict the reviewer has already posted on
this exact SHA by this model, reading every page, since each fix-loop thread
reply adds a review object of its own. A verdict by another model is a second
opinion and never collides; one whose marker names no model, from an earlier
release, collides with every model. A verdict this run did not publish means
the same model under the same account already reviewed this commit, whether
an earlier run or another agent reviewing it now. Hold the review and ask the
user whether to submit, rather than either submitting or standing down. Two
verdicts from one model on one commit read as a single thorough review, so
the overlap goes unnoticed unless
this check catches it. Standing down silently is no better: a second review is
sometimes requested deliberately, and a head both reviews walk away from gets
no verdict at all. If the user says to submit, run `publish` again with
`--second-verdict <earlier-review-id>` and name that review in the body, so a
reader sees the second verdict was deliberate.

When `start-review-loop` runs this skill, it passes `--loop <id>`, and the
loop's record of the reviews it published decides instead, as that skill
describes: a verdict by this model that another loop posted trips the check,
and a second verdict on a head this loop already published needs
`setup --rereview`, the re-review `RESPONDED` asks for.

A head is *clean* only when, after you have settled every earlier finding as
above, no Projector finding thread your model re-checks is open **and this
review posts no P1 or P2 thread**. A `Suggestions` list in the body does not count against it: a review
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

`publish` chooses the review state from the mode, and the draft transition
from the sign-off rule in
[Review as one model among several](#review-as-one-model-among-several):

**Threads open, self-review:** a `COMMENT` review, verdict
`changes-requested`, then the pull request returns to draft with `gh pr ready
<number> --undo`.

**Threads open, cross-author:** a `REQUEST_CHANGES` review, verdict
`changes-requested`. Draft state is left alone.

**Clean head, self-review:** a `COMMENT` review, verdict `clean`, then the
rule decides: the pull request is marked ready with `gh pr ready <number>`
once every model reviewing the head is clean, after the peer wait, and is
left as it is while another model is still reviewing it or a finding thread
is open. That transition is the sign-off a reader sees in the pull-request
list.

**Clean head, cross-author:** a `COMMENT` review, verdict `clean`; say
plainly in the body that the head looks clean and that a human approval is
what remains. It posts a real `APPROVE` only where `review.allow_approve` is
`true`, and only when the rule, counting this verdict as posted, signs the
head off after the peer wait: one account's newest approval or change
request sets `reviewDecision`, so an approval would erase another model's
standing change request. Otherwise it posts a `COMMENT`.

Never downgrade a `REQUEST_CHANGES` you could post to a `COMMENT` to work
around a permission failure, and never read GitHub's refusal of a self-review
verdict as one.

Claude Code's auto mode refuses a clean self-review's publish as
self-approval unless the user's Claude Code settings, or the repository's,
allow `Bash(project review publish *)`, a rule `project init` adds to the
user's `~/.claude/settings.json`. For Codex, `init` adds the same allowance to
the user's Codex rules. Run `publish` as a command of its own so the rule
matches it. If the host refuses it anyway, do not retry it in another form:
keep the lock and the body, and ask the user to run `project init` in a
terminal, which adds the rules to their own settings, or to run the publish
command themselves. `init` adds the rules only for a person at a terminal, so
do not run it to add them yourself.

After submitting, `publish` applies the sign-off rule and sets draft state
for a self-review, re-reads the review it posted and the pull request's
`isDraft`, adds the review id to the loop's record, deletes the start
comment, and releases the lock, in that order, so the pull request is never
left with neither a start comment nor its sign-off's draft state. It prints
the sign-off, how long it waited and for which models, and whether it marked
the pull request draft or ready. It records the review as submitted before any of those
steps, so a run that fails partway exits non-zero and running it again
finishes that review instead of posting a second one.

Report the review as done only after `publish` exits 0. Report the SHA, the
verdict, the sign-off, the review id, how many threads it opened, the head of
the full review an incremental review builds on, and the summary's URL when
the next section publishes one. Before a `clean` verdict, settle every
earlier finding first; `publish` refuses one while a finding is open. Never
resolve another reviewer's thread, resolve a finding you have not verified at
this head, claim a newer SHA was reviewed, or merge.

Once the summary below has run or been skipped, remove the scratch worktree,
which a summary reads for context. A `start-review-loop` subagent may instead
keep it for the pull request's next head and remove it when the pull request
closes.

## Summarize the pull request

After the review is published, give the pull request a summary its human
reviewers can read, with the `summarize-pr` skill in
`../summarize-pr/SKILL.md`. The summary comes after the review because it
builds on it: the review has read the whole diff and checked what it claims,
and its open findings become the summary's flags. A pull request too small
to summarize is too small to send for review. Summarize when both hold.

1. **Summaries are on.** `project config get review.summarize --default true`
   prints anything but `false`.
2. **The repository hosts summaries.** `project site status --repo
   <owner>/<repo> --pr <number>` exits 0 and prints the summary's URL. Without
   a Projector site there is nowhere to publish unattended, so skip the
   summary rather than publishing an Artifact nobody asked for.

While both hold, the summary is not optional. Summarize every pull request
you review, whoever opened it. A summary matters most on another author's
pull request, because that is how its reviewers read the change. Neither
the author, a brief, nor your own judgment is a reason to skip or hold one.
The one off switch is `review.summarize = false` in `.projector.toml`,
and the user sets it.

Then follow `summarize-pr` for this head, with two differences from a
summary a person asks for:

- **Update rather than start over.** When the summaries ref already holds a
  summary of this pull request, start from its newest version and update it
  as the skill's "Update the page when the pull request moves" section
  describes, so a fix-cycle head costs a revision, not a rewrite. The newest
  version can come from another session or account, and can be at this very
  head. Build on it all the same.
- **Carry the review into it.** Every finding thread still open after this
  review, from the query in § Verify earlier findings, becomes a `flag` check on its
  file at its `line`, saying what the finding says, so a reader sees what
  the reviewer flagged under the code it concerns. Drop a `flag` check whose
  finding thread has since been resolved.

Publish it with `project summary publish`, after the review. It then
links the summary from the pull request, so a reader on GitHub finds it:
one comment per pull request, which a later head's summary reposts at the
end, deleting the old one, and one line at the very bottom of the
description. Do not post the link yourself.

The description edit is intended, on every pull request. `publish` appends
its link block after everything else in the description, below a trailer
such as a `Ticket:` line too. It removes the block an earlier publish left
and changes nothing else. It does not count as editing the pull request
body, so a brief that forbids body edits does not cover it. Never hold the
publish for such a brief, and never move or delete the block by hand. The
next publish puts it back at the end.

A repository's own instructions outrank this skill. Where its `AGENTS.md`,
`CLAUDE.md`, or other instructions forbid agents to edit pull request
descriptions, publish with `--no-describe`. The summary still publishes,
and the comment alone links it.

A summary that fails to build or publish never changes the review's
verdict: say in your report that the summary failed and why, and leave the
review as it was.

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
