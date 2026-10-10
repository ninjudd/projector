---
status: ready
priority: next
---

# Let several models review the same pull request

Review loops that run different models on one pull request each publish their
own verdict without colliding, and the pull request is signed off only when
every model reviewing its head finds it clean.

## 1. Problem

A review loop (`start-review-loop`) reviews every head pushed to the
operator's pull requests and publishes each review with
`project review publish`. Every review posts as one GitHub account, the
reviewer, which is usually the operator. Projector treats that account as the
reviewer's identity. That is deliberate: a second loop under the same account
is usually an accident, such as a session left running on a second machine,
and two verdicts from one account on one commit read as one review.

Sometimes the operator runs a second loop on purpose. On an important change
they start review loops in Claude Code, in Codex, and with a third model, to
get separate opinions. All of them post as the same account, so Projector
reads them as one reviewer, and four mechanisms break:

- **The collision check refuses the second model.** `check_collision` in
  `src/projector/review.py` refuses a publish when the account already has a
  verdict on the head that this loop's record (`loops/<loop>/published.json`)
  does not hold, and it tells the loop to ask the user which loop continues.
  A Codex loop's verdict stops the Claude loop's publish, and the other way
  round.
- **Two models on one machine share local state.** The lock
  (`prs/<owner>/<repo>/<pr>-<sha>.lock`), the state file
  (`prs/<owner>/<repo>/<pr>.json`), and the scratch worktree
  (`worktrees/<owner>/<repo>/<pr>/<sha>`) are keyed by pull request and head.
  A second model's `setup` refuses on the first model's lock. Once that lock is
  released, the second `setup` overwrites the first model's state, and
  `add_worktree` force-removes a worktree the first model may still be
  reading.
- **The last publish sets the draft state.** On a self-review, `publish` marks
  the pull request ready on a clean verdict and drafts it on changes
  requested. If Codex requests changes and Claude then finds the head clean,
  Claude's publish marks it ready, and Codex's objection disappears from the
  pull-request list.
- **Each model settles the others' findings.** Every review settles every
  Projector finding thread, and a clean verdict needs every thread closed.
  Claude can withdraw a Codex finding, Codex can reopen it on its next head,
  and neither verdict is independent of the other.

The operator asked for three opinions and gets one, or has to stop all but
one loop.

## 2. Solution

Projector identifies a reviewer by its model, not its account. Within the
reviewer's account, a reviewer is the model ID that every review marker
already records, such as `claude-opus-5-5` or `gpt-5.5`. Each model keeps its
own lock, state file, scratch worktree, and collision check. On the review
side, each model re-checks the findings it opened, and adopts the findings of
a model that has stopped reviewing the pull request, so no finding goes
without a re-check. The fix loop still answers every finding from every
model. The same model twice, on one machine or two, still collides. A
different model is a second opinion and never collides. One rule over
GitHub's state decides the sign-off: a head is signed off when every model
with a verdict on it is clean, no model is still reviewing it, and no
Projector finding thread is open. Every `publish`
applies that rule after it submits, so the last model to finish sets the
draft state. Before `publish` marks a draft ready, it waits a bounded time
for a model that reviewed an earlier head to start on this one.
`project review status` prints the rule's result for `fix-pr` and for you,
and the site shows the result with each model's verdict.

### 2.1 Reviewer identity

A reviewer's identity is the model ID that `--model` gives
`project review setup`, which the start comment, the review marker, and the
finding marker record. Two IDs name the same reviewer when they are equal
after one normalization: a trailing bracketed variant, such as the context
window in `claude-opus-5-5[1m]`, is removed. The reasoning effort and the
Projector version are not part of the identity, so one model at two efforts
is the same reviewer run twice.

| Two reviews by | Same reviewer | Result |
| --- | --- | --- |
| `claude-opus-5-5` on two machines | Yes | The second collides. |
| `claude-opus-5-5` and `claude-opus-5-5[1m]` | Yes | The second collides. |
| `claude-opus-5-5` at effort `high` and at effort `low` | Yes | The second collides. |
| `claude-opus-5-5` and `claude-sonnet-5-5` | No | Two opinions. |
| `claude-opus-5-5` and `gpt-5.5` | No | Two opinions. |
| `claude-opus-5-5` and `claude-opus-5-6` | No | Two opinions. |

The identity holds within one account, so a reviewer is an account and a
model. Verdicts and findings have different scopes:

- **Verdicts and start comments come from the reviewer's own account.** The
  collision check and the sign-off rule read only those, as the collision
  check does today. A Projector verdict from another account is a
  cross-author review, which moves GitHub's `reviewDecision` by itself.
- **Finding threads come from every account.** The sign-off rule and the
  census read every Projector finding thread on the pull request, keyed on the
  finding marker and never on a login, as `census` reads them today. Any open
  one holds the sign-off. A finding from another account is re-checked by the
  account and model that opened it while that reviewer is still reviewing,
  judged from that account's own posts, and adopted by a model of this
  account once it has stopped, by the rules in § 2.8.

The one change from today is who re-checks another account's finding. Today
the operator's loop settles it as its own. After this change the operator's
loop leaves it to the reviewer that opened it while that reviewer is still
running, and adopts it when that reviewer stops.

A model upgraded in the middle of a pull request is a new reviewer. A loop
restarted on `claude-opus-5-6` reviews later heads under that ID. The old ID
owes the next head, or the next answer on its findings, and posts nothing, so
once `review.peer_wait` has passed it has stopped. The new ID, or any other
model still reviewing, then adopts the old ID's findings and re-checks them
as § 2.8 describes. The first adoption reply records the stop on GitHub, and
from then on the old ID's verdicts no longer count, even one on the current
head.

### 2.2 The flow on a new head

Each model runs its own loop, with its own watcher, subagents, and loop id.
One model's review of one head runs like this:

1. The loop's watcher reports `NEW PR`, `NEW HEAD`, or `RESPONDED`, as today.
   A `RESPONDED` reaches every model's loop, whichever model's finding the
   author answered. It also reaches every loop when the last open thread is
   resolved, even when another model resolved its own finding. Before it
   re-reviews, the subagent runs `project review census <number> --wait --json`
   and `project review status <number>`. It re-reviews only when its model's
   newest verdict on the head requests changes, when its model has no verdict
   on the head, or when an answered thread is its own or one it may now adopt
   (§ 2.8). Otherwise it reports that the event belongs to another model and
   stops, posting no start comment.
2. The subagent runs `project review setup <number> --model <model-id>`.
   Setup takes this model's lock, writes this model's state file, and creates
   this model's scratch worktree, at the paths in § 2.4. Before it posts, it
   lists the pull request's start comments. It refuses when a live start
   comment, as § 2.3 defines one, names this same model and is not the one
   this machine's state file for that model records. The refusal names the
   comment and says to delete it if that session has stopped. Setup then
   posts the start comment, whose marker now names the model, and records
   when the review began on this head.
3. The subagent reviews the head as `review-pr` describes. It re-checks every
   finding thread that census marks as its own: the ones its model opened,
   the ones it adopted, and the ones that name no model, which earlier
   releases opened. It also re-checks each thread census says it may adopt,
   and settles an open one, or a fix that does not hold, with
   `project review adopt` (§ 2.8). It leaves the threads of a model that is
   still reviewing to that model.
4. The subagent runs `project review publish`. Publish checks the head, runs
   the collision check for this model only, counts the threads census marks
   `mine` for the verdict, and submits the review. Each finding marker carries
   `model=`.
5. Publish reads the account's verdicts, the start comments, and every
   finding thread, and applies the sign-off rule in § 2.3. When the rule says
   to sign off a draft, publish first runs the peer wait.
6. Publish re-reads the review and the draft state, records the review and
   its model in the loop's record, deletes the start comment, and releases
   this model's lock, as today.

Every other model's loop runs the same flow. Each publish applies the rule
after it submits, so the last publish to run sees every verdict, and the
draft state it sets is final.

### 2.3 The sign-off rule

`signoff()` in `src/projector/review.py` reads three things from GitHub: the
reviewer account's Projector verdicts on the pull request, its start comments,
and every Projector finding thread from any account, the scopes § 2.1 sets.
A start comment is live unless its model
has a verdict on the comment's `sha=` submitted after the comment was
created, or the comment is more than a day old, the age at which a lock is
stale. A model is reviewing the head while its start comment on that head is
live. A model is recorded as stopped while an adoption reply that names it
(§ 2.8) is newer than anything it has posted since. The rule leaves the
verdicts of a recorded-stopped model out, because the model that adopted its
findings now speaks for them. For every other model, the verdict that counts
is its newest one on the current head.

| Result | When | Draft state on a self-review |
| --- | --- | --- |
| `changes-requested` | Any counted model's newest verdict on the head requests changes. | Draft. |
| `waiting` | No counted model requests changes, and either a model is reviewing the head or a Projector finding thread is open. | Left as it is. |
| `clean` | At least one counted model has a verdict on the head, every counted model's newest verdict on it is clean, no model is reviewing it, and no Projector finding thread is open. | Ready, after the peer wait. |
| `unreviewed` | No counted model has a verdict on the head. | Never the result of a publish. |

A live start comment on an older head does not hold the rule. Its model is
either moving to the current head, which the peer wait covers, or stopped,
and a stopped review of a head nobody is looking at should not hold a newer
one.

`waiting` leaves the draft state alone. A draft stays a draft until the last
model finishes. A pull request that is already ready stays ready while a new
head is reviewed, as it does today until a review requests changes.

An open finding thread holds the sign-off whichever model opened it. When the
model that opened it stops reviewing, another model adopts the thread and
settles it, as § 2.8 describes, so a finding never waits on a loop that is
gone.

#### The peer wait

The rule sees only what is on GitHub, and a loop that has not noticed a push
yet has posted nothing for the new head. So before publish marks a draft
ready, or posts an approval, it looks for peers. A peer is another model,
not recorded as stopped, with a verdict on an earlier head of this pull
request, or with a live start comment on another head. A peer is pending when
it has neither a verdict nor a live start comment on the current head. Once a
model's findings are adopted, no publish waits for it again until it posts.

Publish waits for pending peers until `review.peer_wait` seconds after this
review began on the head, re-reading the start comments and verdicts every 15
seconds. The review began when setup posted the start comment, or when move
edited it for this head. Each peer ends the wait in one of three ways:

- **It posts a start comment.** The result becomes `waiting`, so publish
  leaves the draft state for that peer's publish to set.
- **It posts a verdict.** The verdict joins the rule, which publish applies
  again.
- **It posts nothing by the deadline.** The peer is absent, and the result
  stands.

A review that already took longer than `review.peer_wait` does not wait. The
wait catches a loop that is running but slower to notice a push. A loop that
has stopped costs at most what is left of the wait, once for each sign-off.
A loop that stopped in the middle of reviewing the current head leaves a live
start comment on it, which holds the sign-off for up to a day, as its lock
does today. Restarting that model's loop resumes the review, and deleting the
comment releases the hold.

#### Cross-author reviews

On another author's pull request, publish leaves the draft state alone, as
today. A changes-requested verdict posts `REQUEST_CHANGES`. The rule decides
only whether a clean verdict posts an `APPROVE` or a `COMMENT`, and only where
`review.allow_approve` is `true`. Every model posts as one account, and
GitHub's `reviewDecision` follows that account's newest approval or change
request, so one model's approval would replace another model's standing
`REQUEST_CHANGES`. Publish therefore applies the rule before it submits,
counting its own verdict as posted, and approves only on `clean`, after the
peer wait. Two models that finish at the same moment each see the other still
reviewing, so both post a `COMMENT`, and a person approves.

### 2.4 Data shapes

The markers name the model:

| Marker | After this change |
| --- | --- |
| Start comment | `<!-- projector-start v=1 sha=<full-sha> model=<model-id> -->`. A start comment without `model=` is read by the model its first line names, `model <model-id>`, which every start comment carries. |
| Finding | `<!-- projector-finding v=1 priority=<P1\|P2> sha=<full-sha> model=<model-id> -->`. A finding without `model=` belongs to every model. |
| Verify reply | `<!-- projector-verify v=1 result=<result> sha=<full-sha> model=<model-id> -->`. An adoption reply adds `adopted-from=<model-id>`, the model whose finding it takes over. |
| Review | Unchanged, because it already carries `model=`. A verdict whose marker names no model collides with every model. |

Local state moves under the model, in the review state directory:

| Today | After this change |
| --- | --- |
| `prs/<owner>/<repo>/<pr>.json` | `prs/<owner>/<repo>/<pr>/<model-key>/state.json` |
| `prs/<owner>/<repo>/<pr>-<sha>.lock` | `prs/<owner>/<repo>/<pr>/<model-key>/<sha>.lock` |
| `prs/<owner>/<repo>/<pr>-review.json` | `prs/<owner>/<repo>/<pr>/<model-key>/review.json` |
| `worktrees/<owner>/<repo>/<pr>/<short-sha>` | `worktrees/<owner>/<repo>/<pr>/<model-key>/<short-sha>` |
| `loops/<id>/published.json`, entries of `repo`, `number`, `sha`, `review_id`, `verdict`, `published_at` | The same file, and each entry adds `model`. |

`<model-key>` is the identity in lowercase, with every character other than a
letter, a digit, `.`, `_`, or `-` replaced by `-`. For example,
`anthropic/claude-opus-5-5` becomes `anthropic-claude-opus-5-5`. Setup
refuses a model ID that is empty, contains whitespace, or has a key that
starts with `.`. The state file adds `head_started_at`, when the review began
on its current head.

Every command after setup finds its state by pull request and model. `--model`
names the model. Without it, a command uses the one model with state for that
pull request on this machine, and it refuses, listing the models, when there
are several. State that an earlier release wrote is not read, so a review set
up before the upgrade runs `setup` again.

`project review census` gives each thread these fields, by the rules in
§ 2.8:

| Field | Value |
| --- | --- |
| `model` | The model that opened the thread, or `null` when its marker names none. |
| `verifier` | The model that re-checks it now, or `null` when the model that opened it has stopped and no model still reviewing has adopted it. |
| `mine` | `true` when this model is the verifier, or the thread names no model. |
| `adopt` | `now` when this model may adopt it, `wait` when its verifier owes a review whose `review.peer_wait` window is still open, or `no`. |
| `adopt_at` | With `wait`, when the window closes. |
| `answered` | `true` when the author's reply is newer than the verifier's last verify reply on the thread. |

With `--wait`, census waits, re-reading every 15 seconds, until no answered
thread is at `wait`. The count, which the review body prints, covers the
threads marked `mine`. When other models have threads, the count adds them on
a second clause:

```
4 finding threads from claude-opus-5-5: 4 resolved, 0 open. Other models: 2 threads, 1 open
```

When only this model's threads, or threads that name no model, exist, the
count reads as it does today.

`project review status <number>` prints the sign-off of the current head:

```
$ project review status 66
#66 at 4f2a9c1: waiting
  claude-opus-5-5   clean        review 3301
  gpt-5.5           reviewing    start comment 2290, since 14:02 UTC
  1 open finding thread: src/projector/review.py:648 (gpt-5.5)
```

With `--json` it prints one object. It exits 0 whatever the sign-off, and 1
only when it cannot read the pull request. It finds the reviewer as setup
does: `--reviewer`, then `review.username`, then the authenticated user.

| Field | Value |
| --- | --- |
| `repo`, `number`, `head` | The pull request and its current head SHA. |
| `draft` | Whether the pull request is a draft. |
| `signoff` | `clean`, `changes-requested`, `waiting`, or `unreviewed`, by the rule in § 2.3. |
| `models` | One entry for each model with a verdict on the pull request or a live start comment: `model`, and `state`, which is `clean`, `changes-requested`, `reviewing`, `pending`, or `stopped`. A verdict adds `review`, `url`, and `at`. A start comment adds `comment` and `since`. A stopped model adds `adopted_by`. |
| `open_threads` | Each open Projector finding thread's `path`, `line`, `model`, or `null` when the thread names none, and `verifier`. |

`pending` is a model whose only verdicts or start comments are on earlier
heads. It does not change `signoff`, which depends only on the current head,
so `status` and the rule never disagree. `stopped` is a model recorded as
stopped, as § 2.3 defines it.

A new configuration key bounds the wait:

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `review.peer_wait` | non-negative integer | `180` | `project review publish`, as the seconds after a review began on a head that it waits for a model that reviewed an earlier head to start on this one before it signs off. `0` turns the wait off. |

Publish reads the key from the review's checkout, as it reads
`review.allow_approve`. A value that is not a non-negative integer, such as a
string, a negative number, or `true`, makes publish refuse and name the key.
The default covers a peer whose watcher polls every minute or two, plus the
time its loop takes to route the event and post a start comment.

On the site, each head's `review` keeps its one-word `status` and adds the
models:

| Field | Value |
| --- | --- |
| `status` | `changes-requested` when any model's newest verdict on the head requests changes, `clean` when every one is clean, and `unreviewed` when none names the head. |
| `url` | The review that decides `status`: the newest changes-requested verdict, or else the newest clean one. |
| `models` | One entry for each model with a verdict on the head: `model`, `status`, and `url`. Changes requested come first, then models in ID order. |

The site reads no start comments or threads, so it never shows `waiting`.

### 2.5 What reads the sign-off

| Reader | After this change |
| --- | --- |
| Draft state | `publish` sets it by the rule in § 2.3, as § 2.2 describes. |
| `fix-pr`'s outstanding work | No change. `fix-pr` fixes or declines every unresolved finding, from every model, as § 2.8 sets out. |
| `fix-pr`'s clean test | "A clean verdict names its current head" becomes "`project review status <number>` reports `clean`". When nothing is outstanding but the head is not signed off, `fix-pr` reports what `status` names, such as `gpt-5.5` still reviewing. A Projector loop that runs on Codex is one of those models. The external reviewers `fix-pr` names, such as Codex's own GitHub reviewer, post from their own accounts and stay outside the rule. |
| `start-fix-loop`'s watcher | No change. It reports every unresolved thread as `FINDING`, whichever model opened it, and announces each review by its id, so each model's review is one `REVIEW` line. `DRAFT` follows the draft state, which the rule sets, and `VERDICT` follows GitHub's `reviewDecision`. |
| `start-review-loop`'s watcher | No change to the script. It reports an answer on any model's finding thread to every model's loop. Each loop's subagent decides from `project review census --wait` whether the answer is its to act on, as § 2.2 says. |
| Summary page header | Built from `models`, as below. |
| Reviews index | Each row shows its newest head's status through `statusHtml` in `site/src/summary.ts`, the function the header uses. The row keeps the one word, `status`, linked to `url`, because a row has room for one word. `status` keeps its meaning, so the row needs no change. |
| Summaries | No change. There is one summary per head. Each model's review updates the newest summary, as `review-pr` already does when the newest version comes from another session, and its flags come from every open finding thread. |

A merged or closed pull request shows **Merged** or **Closed** in both
places, as it does on `main`. Otherwise the summary page's header shows the
verdict alone when one model reviewed the head, as it does today. With
several models, it groups them by verdict, changes requested first, and each
model's name links to that model's review. The first word of the header is
the word the Reviews index shows.

```
#66 · Clean
#66 · Clean (claude-opus-5-5, gpt-5.5)
#66 · Changes requested (gpt-5.5) · Clean (claude-opus-5-5)
#66 · Unreviewed
```

### 2.6 With incremental reviews

The incremental review plan, `docs/projects/incremental-review/readme.md`,
lets a loop publish a short clean verdict for a minimal push in place of a
full review. The two plans fit together without either one changing the
other's design:

- **An incremental review builds on its own model's full review.** That
  plan's rules require the earlier full review to be in this loop's record
  and to name the same Projector version, model, and effort. A model's
  incremental review never rests on another model's review, so a second model
  reviews every head in full until its own full review allows an incremental
  one.
- **An incremental review is a verdict.** Its review marker names the head
  and the model, so the sign-off rule counts it like any other. Its
  `projector-incremental` line does not change that.
- **An incremental review cannot sign off past another model's finding.**
  That plan's rule that no finding is open reads this model's census, the
  threads marked `mine`, and a thread this model may adopt counts as its own
  for that rule. So a stopped model's open finding leads to a full review,
  which adopts it. The sign-off rule still counts every open thread, so an
  incremental clean verdict leaves the head `waiting` while the finding of a
  model still reviewing is open.
- **The two plans share their parsing.** That plan adds
  `verdict_marker(body)`, which returns a body's verdict, head, and
  incremental-from head, and `reviewer_reviews`, one listing of the account's
  Projector reviews. This plan has `verdict_marker` also return the model, and
  feeds that one listing to the collision check, the incremental rules, and
  the sign-off. Whichever plan is built second extends what the first one
  added.
- **The site names an incremental review per model.** That plan adds
  `incrementalFrom` to a head's clean status. Here it sits on the model's
  entry in `models`, and on the status as well when one model reviewed the
  head. The header names it after the model:
  `#66 · Clean (claude-opus-5-5 incremental from a8cf3d5, gpt-5.5)`.

An incremental review finishes in under a minute, so it is the review most
likely to wait for a peer. It waits only when the pull request is a draft.

### 2.7 Files that change

| File | Change |
| --- | --- |
| `src/projector/review.py` | `identity()` and `model_key()`. `Paths` takes the model and puts the state, lock, request, and worktree paths under it. A resolver picks the model for commands after setup. `start_comment()` writes `model=`. `start_comments()` lists them with their model, SHA, and time, and applies the live rule. `setup` refuses another session's live start comment by the same model and records `head_started_at`. `move` updates it. `verdict_marker()` also returns the model. `check_collision` compares identities. `activity()` reads when each model last posted and whether it owes a review, and `verifier()` applies the rules in § 2.8, for `census`, `adopt`, and `signoff()`. `census` gives each thread the fields in § 2.4, counts the threads marked `mine`, and waits with `--wait`. `adopt()` runs the steps in § 2.8. `publish` writes `model=` into each finding marker, applies `signoff()` with the peer wait, records `model` in the loop's record, and reports the sign-off. `status()` returns the object in § 2.4. The layout comment at the top of the module describes the new paths. |
| `src/projector/cli.py` | `--model` on `move`, `census`, `release`, `publish`, and `gate`. `census --wait`. `review adopt <number> <thread-id>` with `--result` and `--body`. `review status <number>` with `--reviewer` and `--json`. `publish`, `census`, and `adopt` read `review.peer_wait`. |
| `src/projector/site/__init__.py` | `review_statuses` reads each body with `verdict_marker`, groups each head's verdicts by model, and gives `status`, `url`, and `models`. `review_row` passes the same `review` to the Reviews index, as it does on `main`. |
| `site/src/globals.d.ts`, `site/src/summary.ts` | `ReviewStatus` gains `models`. `statusHtml` keeps the one word for the Reviews index, and the header adds the model groups in § 2.5. |
| `site/assets/summary.js` | Rebuilt with `npm run build`. |
| `skills/review-pr/SKILL.md` | A section on reviewing as one model among several: the identity, the collision check per model, which threads you re-check and when you adopt one with `project review adopt`, and the sign-off rule in place of "draft means changes are needed". The labels section gives the start, finding, and verify markers their `model=`, and the adoption reply its `adopted-from=`. The leftover start comment rule compares the comment's model. Run `publish`, `adopt`, and `census --wait` with a timeout of at least `review.peer_wait` plus two minutes. |
| `skills/start-review-loop/SKILL.md` | Pass `--model` to every `project review` command. On `RESPONDED`, run `census --wait` first and re-review only as § 2.2 says. Find the last reviewed SHA from this model's verdicts. The collision paragraph says another loop of the same model. Give each model's loop its own id, such as `<repo>-review-<model-key>`, because two loops that write one record can lose an entry. |
| `skills/fix-pr/SKILL.md` | The clean test and report in § 2.5. A sentence that every unresolved finding is outstanding, whichever model opened it, and that which model re-checks it is the review's concern, not the fix's. |
| `skills/start-fix-loop/SKILL.md` | `DRAFT` clears when every reviewing model signs off, not when one loop does. |
| `docs/cli.md` | The review section, with the per-model state, `--model`, the collision check, the peer wait, `census --wait`, `review adopt`, and `review status`. The `review.peer_wait` row. The header text for several models. |
| `tests/test_review.py`, `tests/test_site.py`, `tests/test_watchers.py` | The tests in § 4. |

### 2.8 Who re-checks a finding

Two kinds of loop act on a finding, and this plan changes only one of them.
The fix loop answers every finding, from every model, exactly as it does
today. The review side re-checks findings at each head, and that work is
split among the models so that two models still reviewing never touch each
other's findings, and a finding whose model has stopped still gets
re-checked.

| Who | Which findings | What it does |
| --- | --- | --- |
| The fix loop (`fix-pr`, run by `start-fix-loop`) | Every unresolved finding, from every model, plus body-only findings and standing change requests, as today | Fixes and pushes, or declines with a reply. Its watcher reports each unresolved thread as `FINDING`, whichever model opened it. |
| The model that opened a finding, while it is still reviewing the pull request | Its own findings, open or resolved | Re-checks each one at every head: resolves a fix that holds, reopens one that does not, and accepts or keeps a decline. |
| The model that adopts a finding | The findings of a model that has stopped reviewing the pull request | The same re-check, with a reply that names the adopting model. |
| Every model | Findings that name no model, which earlier releases posted | The same re-check, as today. |

Which model re-checks a finding never hides it from the fix loop. And an open
finding holds the sign-off, whichever model opened it, until it is settled.

#### When a model has stopped

A model has stopped when it owes the pull request a review and has posted
nothing within `review.peer_wait` seconds of when it began to owe it. Posting
means a start comment, a verdict, or a verify reply on the pull request. A
model with a live start comment has not stopped. A model owes a review in two
cases:

- **A new head.** It has a verdict on an earlier head and none on the current
  head. It began to owe the head when the adopting review began on it, the
  moment the peer wait also counts from.
- **An answer.** An author's reply on a finding it re-checks is newer than
  anything the model has posted. It began to owe the answer when the reply
  was posted.

A model whose verdict names the current head can still owe an answer, so a
loop that stopped just after its verdict is found too.

#### Which model re-checks a finding

The verifier of a finding is, in order:

1. The model that opened it, under the account that opened it, unless that
   reviewer has stopped. Its posts are read from that account, so a finding
   from another account follows the same rule.
2. Otherwise, the model whose adoption reply on the thread came first, among
   models that have not stopped.
3. Otherwise, no one yet. Any model still reviewing the pull request may
   adopt it.

So when two models adopt the same finding, the first adoption reply wins, and
the other model leaves the thread alone. When the opener comes back, by
posting a start comment or a verdict, rule 1 hands its findings back to it.
It re-checks them as its own, its verdicts count again, and the adopter
leaves them alone. The opener may reopen a finding the adopter accepted,
because the finding is its own to judge. When an adopter stops in turn, rule
2 passes the finding to the next model whose adoption reply is on the
thread, or rule 3 lets a model still reviewing adopt it.

#### Adopting a finding

A review re-checks every thread that census marks `adopt: now` by reading it,
exactly as it re-checks its own. A resolved fix that still holds needs
nothing more. When a thread needs an action, a decline to accept or keep or a
fix that does not hold, the review runs one command, so that the wait, the
claim, and the check that the claim won happen in one place:

```sh
project review adopt <number> <thread-id> --result <fixed|accepted|withdrawn|kept|reopened> --body <file>
```

1. It reads the thread and every model's posts on the pull request. It
   refuses when the thread's verifier owes no review, or has posted since it
   began to owe one, or when a model that has not stopped adopted the thread
   first.
2. When the verifier's window is still open, it waits for the window to
   close, re-reading every 15 seconds. It refuses if the verifier posts in the
   meantime.
3. It posts the verify reply. The marker carries `model=` and
   `adopted-from=`, and the first line names both models: "Re-checked by
   `gpt-5.5` for `claude-opus-5-5`, which has stopped reviewing this pull
   request." The body file supplies the rest, as for any verify reply.
4. It reads the thread again. When an earlier adoption reply from a model
   that has not stopped is there, it deletes its own reply and reports which
   model re-checks the thread. Otherwise it applies the result: it resolves
   the thread for `fixed`, `accepted`, or `withdrawn`, unresolves it for
   `reopened`, and leaves it open for `kept`.

Every finding a model adopts counts toward its own census from then on, so
its verdict reflects the adopted findings it keeps open.

The adoption reply records the stop on GitHub. A model whose `review.peer_wait`
window has closed is stopped for the review that adopts its finding. From
then on every reader, the sign-off rule included, treats the model as stopped
while an adoption reply names it in `adopted-from=` and is newer than
anything it has posted. The wait therefore decides once, in the review that
adopts, and every later reader agrees with it without a clock.

## 3. Why this design

- **Identify a reviewer by its model, because the model is what makes a
  second opinion.** The account cannot tell an accident from a choice, since
  every loop posts as the operator's account. The model can. A loop left
  running on a second machine almost always runs the same model, and a loop
  the operator starts in Codex runs a different one. Every review marker and
  start comment already names the model, so the identity needs no table and
  no new field on a review. These identities were rejected:
  - **The account**, today's identity, which is the problem in § 1.
  - **The vendor**, such as Anthropic or OpenAI. It needs a table from model
    IDs to vendors that every new model must update, and it treats Opus and
    Sonnet as one reviewer.
  - **The model family**, the ID without its version numbers. It depends on
    how each vendor names its models. An alias such as `codex-mini-latest`
    has no version to remove, and a family stops a deliberate comparison of
    two versions of one model.
  - **The host**, such as Claude Code or Codex. No marker records it, and
    one host can run models from several vendors.
  - **The loop id.** It is a local name, and `start-review-loop` suggests the
    same name on every host, so a Claude loop and a Codex loop would share
    it.

  The exact ID costs something on an upgrade, when a restarted loop becomes
  a new reviewer. § 2.1 bounds that cost: the old ID stops, its findings are
  adopted, and its verdicts stop counting. Two loops on two versions of one
  model are two reviewers, and stopping one loop ends that.
- **Remove only a bracketed variant.** A context-window variant such as
  `[1m]` runs the same model, so a session that switches it would otherwise
  become a second reviewer. Anything more, such as dropping provider
  prefixes, would guess at naming schemes the rejected family identity
  already guesses at.
- **Key local state by model as well.** One lock per head turns a second model
  into a refusal, and one worktree path per head lets a second setup delete
  the first model's worktree. Keying by loop id instead would let one model
  run twice on one machine, which the lock exists to stop.
- **Let a model re-check its own findings while it reviews, and adopt a
  stopped model's.** An opinion is separate only when another model cannot
  withdraw its findings, and when its clean verdict does not wait on findings
  it did not make. A finding is only safe when some review re-checks it at
  every head: a fix that does not hold has to be reopened, and a decline has
  to be weighed. Adoption gives both. Two models still reviewing never touch
  each other's findings, and a finding whose model has stopped passes to a
  model that is still reviewing. These alternatives were rejected:
  - **Shared re-checking**, today's behavior, which lets two models resolve
    and reopen each other's thread on every head.
  - **Only the opener re-checks.** A model that stops leaves its findings with
    no re-check. A fix that does not hold is never reopened, and a decline it
    would have accepted holds the sign-off forever.
  - **Leaving a stopped model's findings to you.** It turns every stopped loop
    into a chore, and a resolved finding gives you nothing to notice.

  A thread that names no model belongs to every model, so a pull request
  whose findings predate the release that adds `model=` keeps them
  re-checked.
- **Decide a stop once and record it on GitHub.** Whether a model has stopped
  depends on a clock, and every model reads that clock from its own start.
  Letting each reader decide would let two readers disagree about whether a
  verdict counts. The adoption reply decides it once, with `adopted-from=`,
  and every later reader reads the record. A peer that comes back cancels the
  record by posting, and needs no other step.
- **Let the first adoption reply win, and claim inside one command.** Two
  models still reviewing can both find the same stopped model's thread at
  once. `project review adopt` posts the claim, reads the thread again, and
  acts only when its claim came first, so two crossing claims leave one
  action and one reply. Choosing the adopter by a fixed order, such as the
  lowest model ID, was rejected. Every model would have to agree on which
  models are still reviewing, and each one judges that by its own clock.
- **Give a returning model its findings back.** The model that raised a
  finding is the best judge of whether a fix holds. An adoption that stuck
  would keep a running model's findings in another model's hands, which is
  what independence rules out.
- **Hold the sign-off on any open finding.** A clean verdict requires only its
  model's own threads to be settled. Today a finding stays open until someone
  settles it, and an open disagreement is your merge decision. This rule keeps
  that guarantee across models, and adoption keeps it from holding forever.
- **Apply one rule after submitting, in every publish.** The hosts share
  nothing but GitHub, so no coordinator can decide. Applied after the submit,
  the rule runs last in the publish that finishes last, and that publish sees
  every verdict. Two alternatives were rejected:
  - **Applying it before the submit.** Two models that finish together would
    each see the other still reviewing, and neither would sign off.
  - **A claim on GitHub**, such as a comment or label that lists the
    reviewing models. GitHub has no compare-and-set on either, so two loops
    that edit one claim at once lose an update.
- **Leave the draft state alone while waiting.** Drafting on `waiting` would
  draft a ready pull request on every push and mark it ready again after the
  review, and each transition notifies its reviewers. Leaving it matches what
  a ready pull request does today while a new head is reviewed.
- **Wait a bounded time for earlier peers, inside publish.** A clean verdict
  can land before a slower loop has noticed the push. An incremental review
  takes under a minute, less than a watcher interval. A ready pull
  request can be merged at once, so a second opinion that arrives a minute
  later can arrive after the merge. The deadline counts from when the review
  began, so a long review spends it while it runs. These alternatives were
  rejected:
  - **No wait.** It leaves exactly that race open.
  - **Waiting without a bound for every model that reviewed the pull
    request.** A stopped loop would hold every pull request it ever reviewed.
  - **A configured list of required models.** It also closes the race on a
    pull request's first head. But you would edit it each time you start or
    stop a second loop or upgrade a model, and a listed model whose loop
    stopped would hold every pull request. § 7 defers it.
  - **A watcher event that signs off later.** It needs a new event, a status
    query for each draft on each cycle, and a sign-off step that publishes no
    review. The bounded wait reaches the same result inside the publish that
    already runs.
- **Gate an approval on the rule.** On another author's pull request, one
  account's newest approval or change request sets `reviewDecision`, so an
  ungated approval from one model would erase another model's change
  request. A `COMMENT` changes nothing there, so a clean verdict that cannot
  approve comments, as it does today without `review.allow_approve`.
- **Build the rules once and read them everywhere.** `signoff()` serves
  publish, `project review status`, and through `status`, `fix-pr`.
  `verifier()` serves `census`, `adopt`, and `signoff()`. The site reuses
  `verdict_marker()` and the per-model grouping. Neither watcher gets a copy:
  the fix loop's watcher reads the draft state that the rule sets, and the
  review loop's watcher reports every answer and lets `census --wait` decide
  which loop acts on it. Filtering answers by model in the watcher's `jq` was
  rejected, because it would need the adoption rules in a second language.
- **Catch the same model at setup.** Today two machines running one model
  find each other only at publish, after both reviews ran. The live start
  comment shows the duplicate before the second review starts.
- **Show one status and each model on the site.** `status` stays one word,
  so the Reviews index, which shows each row's status, keeps working.
  `models` says which model objected, on the summary page where there is room
  for it. Showing only per-model verdicts would make every reader aggregate
  them again, and showing only the aggregate would hide who objected. The
  index row keeps the one word, because a row holds one status and the page
  it links to names the models.
- **Keep one summary per head.** A summary describes the change, not a
  review, and `review-pr` already builds on a summary another session
  published. One summary per model would multiply pages without new content.
- **Read no state from an earlier release.** A review is in flight for
  minutes, and an upgrade during one is rare. Setting it up again costs one
  setup, while reading the old layout would need code that stays forever.

## 4. Acceptance criteria

The tests run in temporary Git repositories against the fake GitHub in
`tests/test_review.py`, with a fake clock for the wait.

| # | Do | Expect |
| --- | --- | --- |
| 1 | Set up head H as `claude-opus-5-5`, then as `gpt-5.5`, on one machine. | Both succeed, with two locks, two state files, and two worktrees. Publishing one leaves the other's lock, state, and worktree in place. |
| 2 | Set up H as `claude-opus-5-5` twice on one machine. | The second refuses on the lock, as today. |
| 3 | Post a live start comment by `claude-opus-5-5` that this machine's state does not record, then set up as `claude-opus-5-5`. | Setup refuses and names the comment. As `gpt-5.5` it succeeds. It also succeeds when the comment is more than a day old, or when its model has a newer verdict on its SHA. |
| 4 | Publish as `claude-opus-5-5` under a loop when the account has a verdict on H by `gpt-5.5` that the loop did not publish. | Publishes. A verdict by `claude-opus-5-5[1m]`, or one whose marker names no model, refuses as another loop. |
| 5 | Run `publish` without `--model` while two models have state for the pull request. | Refuses and lists both. With one model it proceeds. |
| 6 | Publish `clean` as `claude-opus-5-5` while a finding is open. | Publishes when the finding names `gpt-5.5` and `gpt-5.5` is still reviewing, and the census has its second clause. Refuses when it names `claude-opus-5-5`, when it names no model, and when `claude-opus-5-5` adopted it. |
| 7 | Publish a finding. | Its marker ends with `model=<model-id> -->`. |
| 8 | Publish on a self-review in each case of the table in § 2.3. | Draft for any changes-requested verdict on H. Left as it was while another model is reviewing H or has an open finding. Ready when every model is clean. A start comment whose model has a newer verdict on its SHA is not live, and a live one on an older head does not hold the rule. |
| 9 | Publish `clean` on a draft with a `gpt-5.5` verdict on an earlier head and nothing on H. | Publish waits. A start comment posted during the wait leaves the draft. A verdict posted during the wait joins the rule. Nothing by the deadline marks it ready. A review older than the deadline, `review.peer_wait = 0`, and a pull request that is already ready do not wait. A string, a negative number, or `true` refuses and names the key. |
| 10 | Publish `clean` as two models on H, once with the second model submitting before the first applies the rule, and once after. | Ready either way. With one of them requesting changes, a draft either way. |
| 11 | Publish on another author's pull request with `review.allow_approve = true`. | A clean verdict while another model requests changes or is reviewing posts a `COMMENT`. The last model to go clean posts an `APPROVE`. The draft state does not change. |
| 12 | Run `project review status`. | The text in § 2.4, and with `--json` each field in its table. A model with only earlier-head activity shows `pending` and leaves `signoff` alone. |
| 13 | Answer a `gpt-5.5` finding while `gpt-5.5` is still reviewing, then run `project review census --wait --json` as `claude-opus-5-5`. | `watch-prs.sh` reports `RESPONDED` to both loops, as today. Census returns once `gpt-5.5` posts, with the thread `adopt: no`, so `claude-opus-5-5` does not re-review. An answer on a `claude-opus-5-5[1m]` finding, or on one that names no model, is `mine` and `answered`, so it re-reviews. When `claude-opus-5-5` has a clean verdict on H and `gpt-5.5` resolves the last open thread, its own, `watch-prs.sh` reports `RESPONDED` to both loops, and `claude-opus-5-5` posts no start comment and does not re-review. |
| 14 | Build a site whose head has verdicts from two models. | `review_statuses` gives the aggregate `status`, its `url`, and `models` in order. A test under node renders each header line in § 2.5, and the Reviews index row of the same head shows the header's first word. A merged pull request shows **Merged** in both. |
| 15 | By hand, run a Claude Code review loop and a Codex review loop on a scratch pull request in a scratch repository. | Both review each head without a collision. A finding from one model keeps the pull request in draft after the other model's clean verdict. After the fix, both go clean and the pull request is ready. Then stop the Codex loop, push a fix that does not hold for one of its findings, and the Claude loop reopens that finding as an adoption. The implementing pull request's Testing section records these steps. |
| 16 | Review H as `claude-opus-5-5` while `gpt-5.5` has a verdict on an earlier head, a declined open finding, and nothing on H. Run `adopt --result accepted` on that finding. | Census marks the thread `adopt: wait` until the window closes, then `now`. `adopt` waits out the window, posts a reply whose marker carries `model=claude-opus-5-5 adopted-from=gpt-5.5` and whose first line names both models, and resolves the thread. Census then marks it `mine`, and `status` shows `gpt-5.5` as `stopped`. On a resolved finding whose fix does not hold, `adopt --result reopened` unresolves it. |
| 17 | Run `adopt` while `gpt-5.5` has a live start comment on H, or have `gpt-5.5` post a start comment, verdict, or verify reply during the wait. | `adopt` refuses and leaves the thread and its replies unchanged. Census marks the thread `adopt: no`. |
| 18 | Answer a finding of `gpt-5.5`, whose newest verdict on H requests changes, and let `gpt-5.5` post nothing. | `census --wait` returns when the window after the answer closes, with the thread `adopt: now`. After `claude-opus-5-5` adopts it, `gpt-5.5` is recorded as stopped, and its changes-requested verdict on H no longer counts in the rule. |
| 19 | Run `adopt` from two models on one thread so that both replies post before either reads the thread again. | The second run finds the first reply, deletes its own, and changes nothing else. Census names the first model as the verifier, and the second model's census marks the thread `adopt: no`. |
| 20 | After an adoption, have `gpt-5.5` post a start comment. Then let it stop again. | Census names `gpt-5.5` the verifier again, the adopter's census marks the thread `adopt: no`, and `gpt-5.5`'s verdicts count again. Once `gpt-5.5` has stopped again, the earlier adopter is the verifier with no new reply. |
| 21 | Let the adopter stop in turn. | A third model's `adopt` posts a reply with `adopted-from=` naming the adopter, and becomes the verifier. |
| 22 | Run `watch-threads.sh` on a pull request with unresolved findings from two models still reviewing and from one that has stopped. | It reports a `FINDING` for each thread, whichever model opened it. |
| 23 | Leave a Projector finding from another account open on a self-review, then publish `clean`. | The sign-off is `waiting`, and the pull request stays a draft. Another account's verdicts on H do not change the rule. While that account's model is still reviewing, census marks the finding `adopt: no`. Once it has stopped, census marks it `adopt: now`, and `adopt` takes it over. |

Criteria 1, 4, 6, 8, 10, 16 to 21, and 23 fail before the change: the second
setup refuses on the lock, the collision check refuses the other model, the
census counts every thread as the operator's to settle, the last publish sets
the draft state, and nothing adopts a finding. Criterion 22 passes before and
after the change. It pins that the fix loop still reports every finding.

## 5. Cost

- **Each setup** adds one paginated listing of the pull request's issue
  comments, to find live start comments.
- **Each publish** adds the same listing. The listing of the account's
  reviews serves both the collision check and the rule, and the census
  already runs. The wait runs only when a draft is about to be marked ready,
  or an approval posted, and a peer is pending. Then it lasts at most what is
  left of `review.peer_wait` after the review's own time, with two listings
  every 15 seconds.
- **`project review status`** makes four requests: the pull request, its
  reviews, its issue comments, and its threads. `fix-pr` runs it once for each
  report.
- **An answer on a finding** reaches every model's loop, and each one runs
  `census --wait` once, three requests each 15 seconds while it waits. The
  wait ends when the verifier posts, and at most `review.peer_wait` after the
  answer.
- **An adoption** waits at most what is left of the stopped model's window,
  then posts one reply, reads the thread again, and resolves or unresolves
  it. Once the stop is recorded, no later review waits for that model.
- **The watchers** make no new requests, and neither script changes.
- **The site build** makes no new requests, because it already reads each
  pull request's reviews.
- **A machine that runs several models** keeps one scratch worktree for each
  model and head. Disk use grows with the number of models.
- **Each model reviews every head.** That spending is the point of a second
  opinion. Each review also updates the summary, which deploys the site once,
  as after any review.

Publish reports how long it waited, and `status` shows what held the
sign-off, so the implementing pull request can show the wait on a scratch
pull request with one stopped peer and one running peer.

## 6. Rollout

1. Release the change.
2. Upgrade every machine that runs a review loop with `project upgrade`, then
   restart each loop's watcher, because a running watcher keeps the script it
   started with.
3. Start a second model's loop only after that. An earlier release still
   reads another model's verdict as a collision, and it settles every model's
   findings.

A repository whose site workflow pins an earlier release of the site action
shows the newest verdict of any model for each head until it moves the pin.

## 7. Open questions

None blocks implementation. This one is deferred on purpose:

- **A configured list of required models.** A key such as `review.models`
  could name the models every head needs, which would also cover a pull
  request's first head, where no earlier verdict names a peer. A full first
  review usually takes longer than a second loop needs to notice the pull
  request and post its start comment. The operator decides whether to add the
  key, if a first head is ever signed off before a second model starts.

## 8. Background

Reading the code settled these points, beyond those § 1 names:

- `reviewer_verdicts` lists every review by the reviewer's login whose body
  matches `projector-review .* sha=<head>`, and `check_collision` refuses any
  that the loop's record does not hold. Neither reads the model, although the
  review marker carries `model=` in every shape `tests/test_review.py`
  covers, the older `verdict=approved` shape included.
- `add_worktree` runs `git worktree remove --force` on an existing path before
  it creates the worktree. After a publish, `review-pr` still reads the
  worktree while it summarizes the pull request, so a second model's setup
  can remove it under the first.
- `review_statuses` in `src/projector/site/__init__.py` keeps the newest
  Projector review of each head, whichever model wrote it. The summary page's
  header and the Reviews index row both show its verdict through
  `statusHtml`.
- `watch-prs.sh` counts an author's reply on any open Projector finding
  thread as an answer, so an answer on one model's finding sends `RESPONDED`
  to every model's loop. The plan keeps that and lets `census --wait` decide
  which loop acts.
- `watch-threads.sh` keys `REVIEW` by review id, `DRAFT` by head, and
  `VERDICT` by the reviewed SHA and author, and it reports every unresolved
  thread as `FINDING` without reading its marker. `fix-pr` treats every
  unresolved thread as outstanding. A second model adds lines but no wrong
  ones, and no finding leaves the fix loop's view.
- A second machine that runs a review loop as the same account and the same
  model already trips the collision check in this repository. This plan keeps
  that behavior and catches it earlier, at setup.
