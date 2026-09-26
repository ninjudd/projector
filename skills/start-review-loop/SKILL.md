---
name: start-review-loop
description: Monitor the operator's pull requests in the current GitHub repository, review each exact pushed SHA locally as the operator, and publish verified findings as labeled COMMENT reviews — keeping a pull request in draft until its head is clean. Use when the user asks to keep reviewing current and subsequent PR heads.
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

A wrong reviewer fails quietly, posting review after review as the wrong
account while the watch notices nothing, so the source matters here more than
anywhere else in this skill.

Confirm whatever you resolve under the token actually used:

```sh
gh auth status
gh api user --jq .login
```

The **operator** is the other role these instructions name: the account that
opened the tracked pull requests and whose branches carry the work. By default
it is that same authenticated user, which is why neither role needs
configuring. A reviewer override is what separates them, and every
`<operator>` below means this login, never the reviewer's.

The operator is deliberately not a setting. It is whoever the token says you
are. The watch is scoped by the tracked file rather than by any login, so a
key here would have nothing to configure, and one that disagreed with the
token could only mislabel who did the work.

Watch only the repository containing the current working directory. The loop
reviews every open pull request the operator authors there, from whichever
session opened it, plus any the user explicitly assigns. The tracked set lives
in the tracked file the watcher reads, one `owner/repo#number` per line, and
the watcher sees nothing outside it. Run the watcher with `--repo <owner/repo>
--author <operator>` and it appends each operator pull request it finds open
to that file, so a new one reports as `NEW PR` without anyone adopting it. A
line `!owner/repo#number` keeps one pull request out when the user asks. Hold
validated logins literally in every query you run yourself; never use `@me`. Under the default
the two roles coincide and `@me` looks harmless, but its value follows
whichever token is live, so under a reviewer override it resolves to the
reviewer — an account that authors nothing here — and a listing by `@me` comes
back empty, indistinguishable from a repository with nothing open.

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
  mode, and this file's path, and have it follow the sections from "Review an
  exact head" through "Gate readiness claims in plans".
- Send every later `NEW HEAD` and `RESPONDED` for that pull request to the
  same subagent as a message; never start a second one for it. A `NEW HEAD`
  that arrives mid-review is the moved-head case the start-comment rule
  covers.
- The subagent reports each published review back: the SHA, the verdict, the
  review id, and how many threads it opened. The main loop records the SHA
  and review id. A question for the user, such as the collision check's, also
  comes back to the main loop to ask.
- On `CLOSED`, tell the subagent to finish: delete any start comment it still
  holds and remove its scratch worktrees. Then close the subagent and delete
  the pull request's line from the tracked file.
- When a subagent is lost, to a session restart for example, start a
  replacement under the same name. It rebuilds its context from GitHub: the
  pull request's Projector reviews, their finding threads, and the replies on
  them.

The main loop routes events and records outcomes; it does not inspect code, so
its own context stays small however many pull requests it tracks. Where the
host has no subagents, the main loop runs the same review steps itself.

## Review an exact head

For every new head, in that pull request's subagent:

1. Confirm the pull request remains open and record its full SHA.
2. Fetch that commit and create a scratch worktree at it. Never run a
   reproducer that writes inside the operator's checkout. Never discard,
   restore, or overwrite the operator's dirty or uncommitted work while
   preparing or cleaning up a review.
3. Confirm the scratch worktree's `HEAD` equals GitHub's recorded SHA.
4. Only then post a concise start comment naming the short SHA, carrying the
   review signature line's model and effort segments and a start marker, and
   keep the comment id and `created_at` the call returns:

   ```
   <!-- projector-start v=1 sha=<full-sha> -->
   ```

   ```sh
   gh api repos/<owner>/<repo>/issues/<number>/comments -F body=@<file> \
     --jq '"\(.id) \(.created_at)"'
   ```

5. Inspect the head by the method in `method.md`, next to this file: state
   the change's intent, sort the changed files, run the passes, follow every
   changed definition to the code that depends on it, and verify each
   candidate finding by the four-step protocol before it becomes a thread.
   The written-rules pass reads the head against `../guidelines.md`, the code
   guidelines every Projector skill shares, before the repository's own rule
   documents. Cover both the new range and the pull-request-wide integration
   diff.
6. Run focused tests and reproductions proportional to risk, when the head's
   authors are trusted as the next section defines. A candidate the protocol
   cannot verify is dropped, never posted.
7. Re-fetch the head before publishing. If it moved, the review in progress is
   of a stale head: repeat steps 1 to 3 for the replacement SHA, edit the start
   comment as described next instead of repeating step 4, and revalidate every
   prospective finding against the new head.

### Run a head's code only when its authors are trusted

Running a test, a reproducer, a build, the repository's validation gate, or
any script from the head executes code the head's authors wrote, on this
machine, with the reviewer's GitHub token and credentials in reach. A
malicious test runs as surely as a malicious build step. Reading the code
runs nothing, so a review can always read; it runs the head's code only when
every author of the pull request is trusted.

The authors are the pull request's author and every commit's author:

```sh
gh pr view <number> --repo <owner>/<repo> --json author,commits \
  --jq '[.author.login] + [.commits[].authors[].login] | unique | .[]'
```

A login is trusted when it is the operator, or when the repository grants it
`admin`, `maintain`, or `write`:

```sh
gh api repos/<owner>/<repo>/collaborators/<login>/permission --jq .permission
```

A commit author with no GitHub login, a bot, and a login whose lookup fails
are all untrusted, because none of them shows who wrote the code.

When any author is untrusted, review the head by reading it. The four-step
protocol in `method.md` verifies findings without running anything. Before
you run anything from the head, ask the user through the main loop, naming
the author who is not trusted and the exact command you want to run, and run
it only on a yes. A yes covers the head it was given for; a new head asks
again unless the user said the answer covers the pull request. Publish
without waiting for the answer when reading was enough, and say in the
review's disclosures that the head was reviewed without running its code and
why.

### One start comment per review

A start comment belongs to the review it opens, not to the SHA it first named,
and it lives only until that review is published. When the head moves before
publication — step 7 catches it, or the watcher reports `NEW HEAD` while
inspection is still running — do not post a second start comment. Edit the
existing one so it names the new short SHA, says the head moved from the old
one, and says the review is being updated for the new changes, and set its
marker's `sha=` to the new full SHA:

```sh
gh api -X PATCH repos/<owner>/<repo>/issues/comments/<id> -F body=@<file>
```

`-F` reads the body from the file; `-f` would post the literal path. Re-read
the comment afterwards and confirm it names the new SHA.

Once the review is published and verified, delete the start comment. The
review's signature line carries how long the review took, so the comment has
nothing left to say, and a pull request that keeps one per review is noise a
reader scrolls past:

```sh
gh api -X DELETE repos/<owner>/<repo>/issues/comments/<id>
```

A start comment on a pull request therefore means a review in progress. A
session resuming mid-review looks for one on GitHub rather than in memory and
edits it instead of posting a second one. The exception is a leftover from a
session that stopped between publishing and deleting: a comment whose
`created_at` is earlier than the `submitted_at` of a Projector review on its
`sha=`. Delete that one. Compare the timestamps rather than testing for the
review's presence, because a `RESPONDED` re-review runs on an unmoved head and
its live start comment shares its `sha=` with the verdict that preceded it.

Do not invoke a separate Codex, Cursor, Bugbot, or other reviewer unless the
user explicitly requests that service. This skill performs the local review.

## Label every review and finding

The operator authors the pull request and posts the review, so nothing about
the author line distinguishes a Projector review from a human comment. Two
markers carry that distinction. Both are required, and both are conventions of
this skill — nothing in GitHub enforces them.

**Every review body opens with a signature line and a marker:**

```
🔭 **Projector review** · model `<model-id>` · effort `<effort>` · **<VERDICT>** · took <duration>

<!-- projector-review v=1 verdict=<approved|changes-requested> model=<model-id> effort=<effort> sha=<full-sha> findings=<n> seconds=<n> covered=<read>/<changed> -->
```

State the model and effort **actually running this review** — the model
identifier the host reports for the running session and its reasoning-effort or
thinking level — never a default copied from this file. A review whose
signature misstates what produced it is worse than an unlabeled one: a reader
weighs a finding by what reviewed it.

`<duration>` is how long the review took, measured from the start comment's
`created_at` to the moment the body is composed, in minutes and seconds under
an hour and hours and minutes past it — `took 12m 34s`, `took 1h 04m`. When
the head moved during the review, say how many heads the time covers: `took
14m 02s over 2 heads`. The marker's `seconds=` carries the same span as a whole
number. Compute it in Python rather than with `date`, whose timestamp parsing
differs between platforms:

```sh
python3 -c 'import sys, datetime as d
s = d.datetime.fromisoformat(sys.argv[1])
print(int((d.datetime.now(d.UTC) - s).total_seconds()))' <created_at>
```

The visible verdict word is `APPROVED` or `CHANGES REQUESTED`, matching the
marker's `verdict=`. `approved` means no finding thread on the pull request is
open — a thread outlives the head it was filed on, so findings from earlier
heads count until resolved; `changes-requested` means at least one is open.
Print the census the verdict rests on — `6 finding threads: 4 resolved, 2
open` — counting the threads this review opens among the open, so a
changes-requested review on a fresh head never prints `0 open` above its own
findings. `approved` requires that last number to be zero, and printing it
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

### Outstanding findings

A finding is outstanding while its thread is unresolved, and a head is
**clean** only when no finding thread is outstanding and this review opens no
P1 or P2 thread. This query is the thread half of that test, and it pages so
that a pull request with more than a hundred threads is still read to the end.
Run it by thread state, not by author, and match the marker to separate
Projector findings from ordinary review conversation:

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
tracked file does not prevent this: every loop that adopts the same author's
pull requests tracks the same ones.

A head is *clean* only when the outstanding-findings query above returns
nothing **and this review posts no P1 or P2 thread**. A `Suggestions` list in
the body does not count against it: a review whose only output is that list
is clean. Run the query now, before choosing a branch. Both tests are one-way:
an open thread makes the head not clean whatever inspection found, and a new
thread makes it not clean whatever the query returned. The first matters most
on a fix-cycle head, which arrives
with its threads exactly as the author left them: the reviewer never resolves
threads — only the author does — so a head you inspected and found nothing
wrong in is still not clean while a finding thread is open. The watcher applies
the thread test from its side: `RESPONDED` fires only when every thread is
resolved. `NEW HEAD` cannot, because a push says nothing about threads, so on
every head the check is yours.

**Threads open, self-review:** submit a `COMMENT` review, verdict
`changes-requested`, then convert it to a draft with `gh pr ready <number>
--undo`.

**Threads open, cross-author:** submit a `REQUEST_CHANGES` review, verdict
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
outstanding-findings query returned nothing and the review opens no P1 or P2
thread; after publishing, re-read the review body and the pull request's
`isDraft`, and only then delete the start comment, so the pull request is
never left with neither. Never resolve the author's findings, claim a newer
SHA was reviewed, or merge. Resolving is the author's act, which is why your
verification alone never closes a finding.

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
review. When a pull request closes or merges, close its subagent and delete
its line from the tracked file. The watcher adopts the operator's new pull
requests itself; append one the user assigns, and continue until the user
stops the loop.
