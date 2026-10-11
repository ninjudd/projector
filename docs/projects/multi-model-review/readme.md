---
status: completed
priority: now
---

# Let several models review the same pull request

Review loops that run different models on one pull request each publish their
own verdict without colliding, and the newest verdict on a head signs it off,
whichever model gave it.

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
one loop. § 9 later settled that the last two are how the sign-off should
work, so this plan fixes the first two.

## 2. Solution

Projector identifies a reviewer by its model, not its account. Within the
reviewer's account, a reviewer is the model ID that every review marker
already records, such as `claude-opus-5-5` or `gpt-5.5`. Each model keeps its
own lock, state file, scratch worktree, and collision check. The same model
twice, on one machine or two, still collides. A different model is a second
opinion and never collides. Every model re-checks every Projector finding
thread at each head, as a single model does today, and the fix loop answers
every finding from every model. The newest Projector verdict on a head signs
it off, whichever model gave it: each `publish` sets the draft state by that
verdict after it submits, as § 9 describes. `project review status` prints
the newest verdict for `fix-pr` and for you, and the site shows it as one
status.

### 2.1 Reviewer identity

A reviewer's identity is the model ID that `--model` gives
`project review setup`, which the start comment, the review marker, and the
finding marker record. Two IDs name the same reviewer when they are equal
after one normalization: the ID is lowercased, and a trailing bracketed
variant, such as the context window in `claude-opus-5-5[1m]`, is removed.
The reasoning effort and the
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
model. Each reads a different scope:

- **The collision check and setup's start-comment check read the reviewer's
  own account.** They catch the same model running twice under one account.
- **The newest verdict is read more widely.** It counts the reviewer's
  verdicts and those of every account the repository lets in, as § 9
  describes.
- **Finding threads come from every account.** Every model re-checks every
  Projector finding thread, keyed on the finding marker and never on a login,
  as `census` reads them.

A model upgraded in the middle of a pull request is a new reviewer. A loop
restarted on `claude-opus-5-6` reviews later heads under that ID, re-checks
the old ID's findings like any other, and its newest verdict signs a head off
like any other.

### 2.2 The flow on a new head

Each model runs its own loop, with its own watcher, subagents, and loop id.
One model's review of one head runs like this:

1. The loop's watcher reports `NEW PR`, `NEW HEAD`, or `RESPONDED`, as today.
   A `RESPONDED` reaches every model's loop, whichever model's finding the
   author answered, and each loop re-reviews the head.
2. The subagent runs `project review setup <number> --model <model-id>`.
   Setup takes this model's lock, writes this model's state file, and creates
   this model's scratch worktree, at the paths in § 2.4. Before it posts, it
   lists the pull request's start comments. It refuses when a live start
   comment names this same model and is not the one this machine's state
   file for that model records. A start comment is live until its model
   posts a verdict on its head after the comment was created, or until a day
   has passed since it was last edited, the age at which a lock is stale. The
   refusal names the comment and says to delete it if that session has
   stopped. Setup then posts the start comment, whose marker now names the
   model. When this machine's state for the model records a start comment
   that was never published, setup deletes it, because the new review
   replaces that one.
3. The subagent reviews the head as `review-pr` describes, and re-checks
   every Projector finding thread, whichever model opened it.
4. The subagent runs `project review publish`. Publish checks the head, runs
   the collision check for this model only, and submits the review. Each
   finding marker carries `model=`.
5. Publish re-reads the review. On a self-review it sets the draft state by
   the newest Projector verdict on the head and its open finding threads, as
   § 9 describes. It records the
   review and its model in the loop's record, deletes the start comment, and
   releases this model's lock, as today.

Every other model's loop runs the same flow, and the publish that lands last
sets the draft state.

### 2.3 The sign-off rule

Superseded by § 9. This section signed a head off only when every model with
a verdict on it was clean, no model was still reviewing it, and no finding
thread was open. Its peer wait held a draft for up to `review.peer_wait`
seconds for a model that had reviewed an earlier head, and its cross-author
rule approved only when that whole rule passed. The newest verdict now
decides alone, with no wait.

### 2.4 Data shapes

The markers name the model:

| Marker | After this change |
| --- | --- |
| Start comment | `<!-- projector-start v=1 sha=<full-sha> model=<model-id> -->`. A start comment without `model=` is read by the model its first line names, `model <model-id>`, which every start comment carries. |
| Finding | `<!-- projector-finding v=1 priority=<P1\|P2> sha=<full-sha> model=<model-id> -->`. |
| Verify reply | `<!-- projector-verify v=1 result=<result> sha=<full-sha> model=<model-id> -->`. |
| Review | Unchanged, because it already carries `model=`. A verdict whose marker names no model collides with every model. |

Local state moves under the model, in the review state directory:

| Today | After this change |
| --- | --- |
| `prs/<owner>/<repo>/<pr>.json` | `prs/<owner>/<repo>/<pr>/<model-key>/state.json` |
| `prs/<owner>/<repo>/<pr>-<sha>.lock` | `prs/<owner>/<repo>/<pr>/<model-key>/<sha>.lock` |
| `prs/<owner>/<repo>/<pr>-review.json` | `prs/<owner>/<repo>/<pr>/<model-key>/review.json` |
| `worktrees/<owner>/<repo>/<pr>/<short-sha>` | `worktrees/<owner>/<repo>/<pr>/<model-key>/<short-sha>` |
| `loops/<id>/published.json`, entries of `repo`, `number`, `sha`, `review_id`, `verdict`, `published_at` | The same file, and each entry adds `model`. |

`<model-key>` is the identity, already lowercase, with every character other
than a letter, a digit, `.`, `_`, or `-` replaced by `-`. For example,
`anthropic/claude-opus-5-5` becomes `anthropic-claude-opus-5-5`. Setup
refuses a model ID that is empty, contains whitespace, or has a key that
starts with `.`. Two identities can share a key, such as `a/b` and `a-b`, so
the state file records its identity, and setup refuses when the key's state
names a different one.

Every command after setup finds its state by pull request and model. `--model`
names the model. Without it, a command uses the one model with state for that
pull request on this machine, and it refuses, listing the models, when there
are several. State that an earlier release wrote is not read, so a review set
up before the upgrade runs `setup` again.

`project review status` prints the newest verdict, in the shape § 9 gives.
The site's `review` for each head stays the one status it is on `main`
before this plan, as § 9 describes.

### 2.5 What reads the sign-off

| Reader | After this change |
| --- | --- |
| Draft state | `publish` sets it by the newest verdict on the head and its open finding threads, as § 9 describes. |
| `fix-pr`'s outstanding work | No change. `fix-pr` fixes or declines every unresolved finding, from every model. |
| `fix-pr`'s clean test | "A clean verdict names its current head" becomes "`project review status <number>` reports `clean`", which reads the newest verdict on the head from every account the repository lets in. A Projector loop that runs on Codex is one of those models. The external reviewers `fix-pr` names, such as Codex's own GitHub reviewer, post from their own accounts and stay outside it. |
| `start-fix-loop`'s watcher | No change. It reports every unresolved thread as `FINDING`, whichever model opened it, and announces each review by its id, so each model's review is one `REVIEW` line. `DRAFT` follows the draft state, and `VERDICT` follows GitHub's `reviewDecision`. |
| `start-review-loop`'s watcher | No change. It reports an answer on any model's finding thread to every model's loop. |
| Summary page header and Reviews index | One status, the newest Projector review of the head, drawn by `statusHtml` in `site/src/summary.ts`, as on `main` before this plan. |
| Summaries | No change. There is one summary per head. Each model's review updates the newest summary, as `review-pr` already does when the newest version comes from another session, and its flags come from every open finding thread. When two models update one head's summary at once, the one that publishes last keeps the flags its own census read, so a finding the other model opened in between has no flag until the next summary of that pull request. The finding itself stays on GitHub and in the fix loop's view. |

The header that showed a status several models gave as a button, marked
with an asterisk, with a card listing each model's verdict, is superseded by
§ 9. A merged or closed pull request shows **Merged** or **Closed** in both
places, as it does on `main`.

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
  and the model, so it can be the newest verdict on a head and sign it off
  like any other. Its `projector-incremental` line does not change that.
- **An incremental review needs every finding settled.** That plan's rule
  that no finding is open reads every Projector finding thread, whichever
  model opened it, so an open finding from another model leads to a full
  review.
- **The two plans share their parsing.** That plan adds
  `verdict_marker(body)`, which returns a body's verdict, head, and
  incremental-from head, and `reviewer_reviews`, one listing of the account's
  Projector reviews. This plan has `verdict_marker` also return the model,
  and feeds that listing to the collision check and the incremental rules.
- **The site links an incremental review to its own model's full review.**
  That plan adds `incrementalFrom` to a head's clean status. Its link names
  the newest full review of the earlier head by the same model as the
  incremental review, never another model's.

### 2.7 Files that change

| File | Change |
| --- | --- |
| `src/projector/review.py` | `identity()` and `model_key()`. `Paths` takes the model and puts the state, lock, request, and worktree paths under it. A resolver picks the model for commands after setup. `start_comment()` writes `model=`. `start_comments()` lists them with their model, SHA, and time, and `is_live()` applies the live rule. `setup` refuses another session's live start comment by the same model. `verdict_marker()` also returns the model. `check_collision` compares identities. `verdicts()` and `newest_verdicts()` find the newest verdict on a head. `publish` writes `model=` into each finding marker, sets draft state by the newest verdict, and records `model` in the loop's record. `status()` returns the object in § 9. The layout comment at the top of the module describes the new paths. |
| `src/projector/cli.py` | `--model` on `move`, `census`, `release`, `publish`, and `gate`. `review status <number>` with `--reviewer` and `--json`. |
| `src/projector/site/__init__.py` | `review_statuses` keeps each head's newest review, whichever model wrote it, and links an incremental review to its own model's full review. |
| `skills/review-pr/SKILL.md` | A section on reviewing as one model among several: the identity, the collision check per model, and the newest verdict as the sign-off. The labels section gives the start, finding, and verify markers their `model=`. The leftover start comment rule compares the comment's model. |
| `skills/start-review-loop/SKILL.md` | Pass `--model` to every `project review` command. Find the last reviewed SHA from this model's verdicts. The collision paragraph says another loop of the same model. Give each model's loop its own id, such as `<repo>-review-<model-key>`, because two loops that write one record can lose an entry. |
| `skills/fix-pr/SKILL.md` | The clean test in § 2.5, run once. |
| `skills/start-fix-loop/SKILL.md` | `DRAFT` clears when the newest verdict on the head is clean. |
| `docs/cli.md` | The review section, with the per-model state, `--model`, the collision check, the newest verdict, and `review status`. The header text. |
| `tests/test_review.py`, `tests/test_site.py` | The tests in § 4. |

### 2.8 Who re-checks a finding

Every model re-checks every Projector finding thread at each head, its own
and every other model's, as a single model does today: it resolves a fix
that holds, reopens one that does not, and accepts or keeps a decline. The
fix loop answers every finding from every model. A clean verdict needs every
finding thread settled, whichever model opened it, so the newest clean
verdict on a head vouches for every finding on it. The design this section
held, in which a model re-checked only its own findings and adopted those of
a model that had stopped, is dropped, for the reasons in § 9.

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

  The exact ID costs little on an upgrade: a restarted loop is a new
  reviewer, but it re-checks the old ID's findings, and its newest verdict
  signs a head off like any other. Two loops on two versions of one model are
  two reviewers, and stopping one loop ends that.
- **Remove only a bracketed variant.** A context-window variant such as
  `[1m]` runs the same model, so a session that switches it would otherwise
  become a second reviewer. Anything more, such as dropping provider
  prefixes, would guess at naming schemes the rejected family identity
  already guesses at.
- **Key local state by model as well.** One lock per head turns a second model
  into a refusal, and one worktree path per head lets a second setup delete
  the first model's worktree. Keying by loop id instead would let one model
  run twice on one machine, which the lock exists to stop.
- **Catch the same model at setup.** Today two machines running one model
  find each other only at publish, after both reviews ran. The live start
  comment shows the duplicate before the second review starts.
- **Keep one summary per head.** A summary describes the change, not a
  review, and `review-pr` already builds on a summary another session
  published. One summary per model would multiply pages without new content.
- **Read no state from an earlier release.** A review is in flight for
  minutes, and an upgrade during one is rare. Setting it up again costs one
  setup, while reading the old layout would need code that stays forever.

§ 9 gives the reasons the newest verdict decides the sign-off.

## 4. Acceptance criteria

The tests run in temporary Git repositories against the fake GitHub in
`tests/test_review.py`. Criteria that § 9 superseded or dropped keep their
numbers, so the ones cited elsewhere stay put.

| # | Do | Expect |
| --- | --- | --- |
| 1 | Set up head H as `claude-opus-5-5`, then as `gpt-5.5`, on one machine. | Both succeed, with two locks, two state files, and two worktrees. Publishing one leaves the other's lock, state, and worktree in place. |
| 2 | Set up H as `claude-opus-5-5` twice on one machine. | The second refuses on the lock, as today. |
| 3 | Post a live start comment by `claude-opus-5-5` that this machine's state does not record, then set up as `claude-opus-5-5`. | Setup refuses and names the comment. As `gpt-5.5` it succeeds. It also succeeds when the comment is more than a day old, or when its model has a newer verdict on its SHA. |
| 4 | Publish as `claude-opus-5-5` under a loop when the account has a verdict on H by `gpt-5.5` that the loop did not publish. | Publishes. A verdict by `claude-opus-5-5[1m]`, or one whose marker names no model, refuses as another loop. |
| 5 | Run `publish` without `--model` while two models have state for the pull request. | Refuses and lists both. With one model it proceeds. |
| 6 | Publish `clean` as `claude-opus-5-5` while a finding by `gpt-5.5` is open. | Refuses, as it does for a finding that names `claude-opus-5-5` or no model: a clean verdict needs every Projector finding thread settled. |
| 7 | Publish a finding. | Its marker ends with `model=<model-id> -->`. |
| 8 | Superseded by criteria 24 and 25. | |
| 9 | Superseded by criterion 30: nothing waits. | |
| 10 | Superseded by criterion 25. | |
| 11 | Superseded by criterion 29. | |
| 12 | Superseded by criterion 27. | |
| 13 | Dropped with adoption (§ 9). | |
| 14 | Superseded by criterion 28. | |
| 15 | By hand, run a Claude Code review loop and a Codex review loop on a scratch pull request in a scratch repository. | Both review each head without a collision. A finding from one model keeps the pull request in draft until it is settled. Stop the Codex loop after it requests changes, push a fix, and let the Claude loop review the new head: its clean verdict marks the pull request ready. The implementing pull request's Testing section records these steps. |
| 16 to 21 | Dropped with adoption (§ 9). | |
| 22 | Run `watch-threads.sh` on a pull request with unresolved findings from two models. | It reports a `FINDING` for each thread, whichever model opened it. |
| 23 | Dropped with adoption (§ 9). | |
| 24 | On a self-review, publish `clean` as `claude-opus-5-5` while `gpt-5.5`'s older verdict on H requests changes. | The pull request is marked ready, and `publish` reports its own review as the newest verdict. |
| 25 | On a ready self-review, publish `changes-requested` after `gpt-5.5`'s clean verdict on H. Then publish `clean` while `gpt-5.5` posts `changes-requested` between that publish's submit and its draft step, and publish two models together in either order. | Draft the first time. The second stays a draft, and `publish` names `gpt-5.5`'s review as the newest verdict. Two models together leave the later verdict's draft state. A listing that has not caught up with this review still counts it. |
| 26 | Publish while an account the repository does not let in has a newer Projector verdict on H. | That verdict does not count. |
| 27 | Run `project review status`. | The newest verdict, its model, and its URL, each model's newest verdict when several gave one, newest first, and the open threads, with each `--json` field in § 9. A collaborator's verdict counts without `--reviewer`. A head with no verdict is `unreviewed`, and there is no `waiting`. |
| 28 | Build a site whose head has verdicts from two models. | `review_statuses` gives the newest review's status and URL, with no `models`. The header shows that one status as a link, with no asterisk, button, or card, and the Reviews row shows the same. An incremental review links its own model's full review. |
| 29 | Publish `clean` on another author's pull request with `review.allow_approve = true` while another model's change request is on H and a third model is reviewing it. | It posts an `APPROVE`, and the draft state does not change. |
| 30 | Publish with `review.peer_wait` set, another model's verdict on an earlier head, and its live start comment on H. | Publish reads no `review.peer_wait`, waits for nothing, and marks the pull request ready on its clean verdict. |
| 31 | Publish `clean` as `claude-opus-5-5` while `gpt-5.5` publishes `changes-requested` with a finding after `claude-opus-5-5`'s census and a second before its submit, from a draft and from a ready pull request. | The clean verdict is the newest, and the pull request ends a draft both times, with `publish` reporting one open finding thread. |
| 32 | On another author's pull request, request changes on an earlier head, then publish `clean` on H without `review.allow_approve`. | Publish dismisses the change request, and another model's older change request on H with it, without waiting for a model still reviewing H. It dismisses nothing when a newer verdict on H requests changes or a finding thread is open once this review is submitted, after an `APPROVE`, or for a change request without a Projector verdict marker, even one that mentions the loop, from another account, or older than an approval. A dismissal GitHub refuses exits non-zero, and the retry finishes it. |

Criteria 1 to 7 and 24 to 32 pass in `tests/test_review.py` and
`tests/test_site.py`, and criteria 24 and 27 to 30 fail before § 9's change.
Criterion 31 held before it too, and pins that the newest verdict does not
lose it. Criterion 32 fails before `publish` dismissed anything, when a clean
head left the account's change request standing.
Criterion 22 holds because `watch-threads.sh` reports every unresolved
thread without reading its marker, which `tests/test_watchers.py` pins.
Criterion 15 needs two hosts and has not been run. § 10 records how the
project closed without it.

## 5. Cost

- **Each setup** adds two paginated listings: the pull request's issue
  comments, to find live start comments, and the account's reviews, to tell
  a live start comment from one its model's verdict has ended.
- **Each publish on a self-review** adds one paginated listing of the pull
  request's Projector reviews after it submits, to find the newest verdict,
  and reads the finding threads again. Nothing waits.
- **Each publish on another author's pull request** adds one paginated
  listing of the account's reviews, to find its standing change requests.
  When one stands, it also reads the newest verdict and the finding threads
  as a self-review does. When it dismisses, it makes one request for each
  change request and lists the account's reviews once more to confirm them.
- **`project review status`** makes three requests: the pull request, its
  reviews, and its threads. `fix-pr` runs it once for each report.
- **The watchers** make no new requests, and neither script changes.
- **The site build** makes no new requests, because it already reads each
  pull request's reviews.
- **A machine that runs several models** keeps one scratch worktree for each
  model and head. Disk use grows with the number of models.
- **Each model reviews every head.** That spending is the point of a second
  opinion. Each review also updates the summary, which deploys the site once,
  as after any review.

## 6. Rollout

1. Release the change.
2. Upgrade every machine that runs a review loop with `project upgrade`, then
   restart each loop, because a running watcher keeps the script it started
   with. Restart it under a loop id of its own model, such as
   `<repo>-review-<model-key>`, since loops started from the same suggestion
   share `<repo>-review-loop`.
3. Start a second model's loop only after that. An earlier release still
   reads another model's verdict as a collision.

## 7. Open questions

None blocks implementation. § 9 rejects the one this section deferred, a
configured list of required models.

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
  to every model's loop.
- `watch-threads.sh` keys `REVIEW` by review id, `DRAFT` by head, and
  `VERDICT` by the reviewed SHA and author, and it reports every unresolved
  thread as `FINDING` without reading its marker. `fix-pr` treats every
  unresolved thread as outstanding. A second model adds lines but no wrong
  ones, and no finding leaves the fix loop's view.
- A second machine that runs a review loop as the same account and the same
  model already trips the collision check in this repository. This plan keeps
  that behavior and catches it earlier, at setup.

## 9. The newest verdict wins

After the per-model state, the collision check, the every-model sign-off,
and the site header that listed every model's verdict had merged, the owner
replaced the sign-off with a simpler rule: the newest verdict on a head signs
it off. A review loop on Codex might find problems on one pull request and
then be stopped. Under the every-model rule that model's last change request
held the pull request in draft until something took its findings over, which
is what the adoption design in the earlier § 2.8 existed to do. Under the new
rule the next clean verdict from a model still reviewing signs the head off.
That clean verdict could only be given once every Projector finding thread
was settled, whichever model opened it, and a branch rule that requires every
conversation to be resolved before a merge, as this repository's does, holds
the merge while any thread is open, so nothing a stopped model found can be
merged past unanswered.

The rule:

- **Draft state follows the newest verdict.** After each publish on a
  self-review, `publish` reads the Projector verdicts on the head it
  published on and sets the draft state by the newest one, whichever model
  gave it: draft when it requests changes, ready when it is clean. The
  newest is usually the review it just posted, which it counts as read back
  even when the reviews listing has not caught up. It is another model's when
  that model posted on the head between this review's submit and its draft
  step, and then `publish` names it. Reviews are ordered by when GitHub says
  they were submitted, and then by id.
- **Verdicts count from the accounts the site counts.** The newest verdict is
  read from the reviewer's account and from every account that owns the
  repository, belongs to its organization, or collaborates on it, the rule
  `review_statuses` applies to the site's status. Anyone can review a public
  repository's pull request, so a verdict from any other account is ignored.
- **Nothing waits.** There is no peer wait, no hold while another model's
  start comment is live, and no `review.peer_wait` key. `publish` checks the
  head before it submits, as it did before this plan, and refuses when the
  head has moved since setup.
- **An open finding keeps a draft.** A clean verdict needs every Projector
  finding thread settled when its review counts them, but another model can
  post a finding after that count and before the clean verdict lands, which
  leaves the newer verdict clean over an open finding. So in the same step
  that reads the newest verdict, `publish` counts the open finding threads
  again, from every account, and keeps the pull request a draft while any is
  open, whatever the newest verdict. A branch rule that requires conversation
  resolution checks every thread again when the pull request merges.
- **A cross-author review follows its own verdict.** On another author's pull
  request, `publish` posts `REQUEST_CHANGES` for changes requested, and for a
  clean verdict a `COMMENT`, or an `APPROVE` where `review.allow_approve` is
  `true`, whatever other models said. It leaves the draft state alone.
- **A clean newest verdict dismisses the account's change requests.** A
  `COMMENT` replaces nothing, so on another author's pull request it leaves
  the account's own earlier `REQUEST_CHANGES` holding `reviewDecision` after
  the head is found clean, and the pull request stays blocked on a change
  request its reviews have settled. So when the newest verdict on the head is
  clean and no finding thread is open, read in the same step a self-review
  reads them for its draft state, `publish` dismisses each Projector
  `REQUEST_CHANGES` review by the account that nobody dismissed and no later
  approval replaced. A change request whose body carries no Projector verdict
  marker is a person's, and stays, even when it mentions the loop by name.

`project review status <number>` prints the newest verdict on the current
head, the model and review it came from, each model's newest verdict there
when several gave one, newest first, and the open finding threads. It exits 0
whatever the verdict, and 1 only when it cannot read the pull request. It
counts the verdicts `publish` counts, with the reviewer found as `setup`
finds it: `--reviewer`, then `review.username`, then the authenticated user.

```
$ project review status 66
#66 at 4f2a9c1: clean from claude-opus-5-5, https://github.com/example/app/pull/66#pullrequestreview-3301
  claude-opus-5-5   clean               review 3301, 14:20 UTC
  gpt-5.5           changes-requested   review 3290, 14:02 UTC
```

| Field | Value |
| --- | --- |
| `repo`, `number`, `head` | The pull request and its current head SHA. |
| `draft` | Whether the pull request is a draft. |
| `verdict` | The newest verdict on the head: `clean`, `changes-requested`, or `unreviewed`. |
| `newest` | That verdict's `model`, `verdict`, `review` id, `url`, `at`, and `login`, or `null` when the head is `unreviewed`. |
| `models` | Each model's newest verdict on the head in the same shape, newest first. |
| `open_threads` | Each open Projector finding thread's `path`, `line`, and `model`, or `null` for a thread whose marker names none. |

`fix-pr` runs `status` once. The every-model rule counted only one account's
verdicts at a time, so `fix-pr` ran it with `--reviewer` for each account
that had posted a Projector review. The newest verdict reads every account
the repository lets in, so one run sees the operator's loop and a teammate's
alike, and the operator's own account needs no run of its own: a teammate's
standing `REQUEST_CHANGES` already holds `fix-pr`'s clean test through
`reviewDecision`, and a self-review's sign-off through the draft state.

The site shows one status for a head, the newest Projector review of it,
drawn and linked as it was before this plan, with "incremental from …" when
that review is incremental. The button marked with an asterisk, its card of
each model's verdict, its styles, the note only a screen reader read, and the
`models` list in each page's data are gone. Each model's verdict is still on
GitHub and in `project review status`.

The per-model identity, state, lock, worktree, and collision check stay,
because two models still review one pull request at once. The state file no
longer records `head_started_at`, which only the peer wait read.

These alternatives were rejected:

- **Every model clean**, the rule this section replaces. A stopped model's
  change request held the pull request until another model adopted its
  findings, which needed a census of who re-checks each finding, a wait for a
  model to post before another could take its findings over, and a record of
  stopped models on GitHub. The newest verdict needs none of that.
- **A configured list of required models**, the question § 7 deferred. A
  listed model that stops holds every pull request, as above.

The trade-off is that a second opinion counts only while it is the newest
verdict. When Codex requests changes and a later Claude review finds the
head clean, Claude has settled every Codex finding first, by fix, accepted
decline, or withdrawal, and its verdict stands. If that settlement is wrong,
the open disagreement is the operator's to reopen, as it is for any finding a
review resolves.

## 10. Outcome

The project shipped. #237 gave each model its own identity, state, lock,
worktree, and collision check. #243 made the newest verdict on a head sign it
off, as § 9 describes, and #245 added § 9's dismissal of settled change
requests and criterion 32. Two designs merged and were then replaced: #233's
header, which showed each model's verdict, and #239's every-model sign-off,
both removed by #243. #241, which built the adoption that the earlier § 2.8
described, closed without merging when § 9 dropped adoption. Every merged
pull request here was first released in 0.7.5 and is in 0.7.6, the release
now installed.

On `main` at 0.7.6, every criterion that § 9 kept passes. The tests are in
`tests/test_review.py` unless the table names another file:

| # | Evidence |
| --- | --- |
| 1 | `test_two_models_set_up_one_head_side_by_side_and_publishing_one_leaves_the_other` |
| 2 | `test_a_second_instance_for_the_same_head_exits_without_posting` |
| 3 | `test_another_sessions_live_start_comment_by_the_same_model_refuses_setup` and `test_a_start_comment_a_day_old_or_followed_by_its_models_verdict_is_not_live` |
| 4 | `test_another_models_verdict_on_the_head_is_not_a_collision` and `test_a_verdict_by_the_same_model_or_naming_no_model_is_another_loop` |
| 5 | `test_a_command_after_setup_needs_model_when_two_models_are_set_up` and `test_one_model_with_state_needs_no_model_flag` |
| 6 | `test_a_clean_verdict_needs_another_models_finding_settled_first` |
| 7 | `test_changes_requested_posts_findings_with_the_marker_and_returns_the_pull_request_to_draft` |
| 22 | `test_reports_every_kind_of_outstanding_work_on_a_tracked_pull_request` in `tests/test_watchers.py` |
| 24 | `test_a_clean_newest_verdict_marks_it_ready_over_an_older_models_change_request` |
| 25 | `test_a_newer_change_request_drafts_it`, `test_a_change_request_posted_after_this_clean_verdict_wins`, `test_two_models_that_publish_together_leave_the_later_ones_draft_state`, and `test_the_review_read_back_counts_when_the_listing_lags` |
| 26 | `test_a_verdict_from_an_account_the_repository_does_not_let_in_does_not_count` |
| 27 | `StatusTests` |
| 28 | `test_the_newest_review_of_a_head_decides_whichever_model_wrote_it`, `test_an_incremental_review_builds_on_its_own_models_full_review`, and `test_the_header_shows_the_newest_verdict_alone_when_several_models_reviewed_the_head` in `tests/test_site.py` |
| 29 | `test_a_cross_author_review_approves_on_its_own_clean_verdict_and_leaves_draft_state_alone` |
| 30 | `test_publish_waits_for_no_other_model_and_reads_no_wait_setting` |
| 31 | `test_a_finding_another_model_posts_mid_publish_keeps_the_pull_request_a_draft` |
| 32 | `DismissalTests` |

Nothing the replaced designs added remains: no `review.peer_wait`, no
`head_started_at`, and no `models` list or multi-verdict header on the site.

One later change, outside this plan, qualifies § 9. #244 lets a pull
request's author hold it in draft while `fix-pr` builds a change for it.
While the author's hold comment is open, `publish` keeps a clean newest
verdict a draft and dismisses no change request, as `docs/cli.md` describes.

Criterion 15, the by-hand run of two model loops on one pull request, has
not been observed. Every Projector verdict on this repository's recent pull
requests names one model, `claude-opus-5-5`. That is follow-up evidence to
watch for, not a gap in what shipped. Under § 9, the first pull request that
two model loops review should show both loops reviewing each head without a
collision, a finding from either model keeping the pull request a draft until
it is settled, and, after one loop stops and a fix is pushed, the other
loop's clean verdict marking the pull request ready. A collision between two
different models, or a self-review left a draft under a clean newest verdict
with no open finding thread or hold, is a defect to fix in a new change.
