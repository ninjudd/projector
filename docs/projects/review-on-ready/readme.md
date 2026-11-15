---
status: ready
priority: now
---

# Review a pull request once it is ready for review

Projector reviews a pull request only after its author marks it ready for
review, labels it `reviewing` while a review runs and `clean` once the current
head's review is clean, and, in a repository that relies on Projector alone,
blocks the merge until the current head has a clean review.

## 1. Problem

`review-repo` subscribes a session to review the operator's pull requests. The
watch daemon announces each new head, and the session runs a full or
incremental review of it. On the operator's own pull request, GitHub refuses
both an approval and a change request, so Projector carries the verdict in
draft state instead: `project review publish` converts the pull request to a
draft when a review finds problems, and marks it ready when a review is clean.
Agents open every pull request as a draft and never mark it ready themselves.

Because draft state already means "not signed off", an author has no way to
say "I'm still working on this". Every head gets a review, including heads the
author replaces minutes later. The waste gathers in pull requests whose design
keeps moving after they open, such as a UI change the author tests on a
device. In one repository that Projector reviews, such a pull request got 9
reviews over 26 commits. Three of them in a row were clean, and each was
followed within minutes by another layout change, not by a fix. Another got
three clean reviews in 11 minutes while its author adjusted padding. Each of
those reviews read the whole change and ran the repository's validation gate.

Draft state as sign-off also works against GitHub's own meaning of draft,
which GitHub and the people who use it read as "not ready for review". GitHub
doesn't request code owners' reviews on a draft, for example.

## 2. Solution

Draft goes back to GitHub's meaning: the author is still working. Under a
repository subscription, the watch daemon announces no review of a draft head,
so a pull request gets its first review when its author marks it ready for
review. `project review publish` stops moving draft state. A `reviewing` label
shows a review in progress, and `publish` replaces it with a `clean` label on a
clean head, which a workflow removes when a new commit arrives. In a repository whose default branch requires it, `publish` also posts
a `projector/review` commit status on the reviewed commit, so GitHub blocks the
merge until the current head has a clean review. `project init` asks whether a
repository relies on Projector alone, and a yes sets up that requirement and
the workflow. A repository where people review too, as at most workplaces,
keeps the label and needs no check.

### 2.1 The flow

1. The agent opens the pull request as a draft and keeps working. The daemon
   announces no review of a draft head.
2. When its work is done, the agent runs `project review ready <number>`, which
   marks the pull request ready for review. When its hand-over posts a test
   request, the pull request stays a draft until the person answers: `Looks
   good` or a decline marks it ready, and `Some feedback` keeps it a draft
   while the agent makes the changes.
3. The daemon sees a head that isn't a draft and has no verdict by this model,
   and announces `NEW PR` or `NEW HEAD`. The session reviews the head as it
   does today. `project review setup` adds the `reviewing` label when it posts
   the review's start comment.
4. `publish` posts the review, then works out whether the head is signed off
   (§ 2.2). In one label change it removes `reviewing` and adds or removes
   `clean`, and it posts the `projector/review` status when the default branch
   requires it.
5. When the review requests changes, `fix-pr` pushes its fixes to the pull
   request, which stays ready, and the daemon announces each push for review
   as it does today.
6. When the author must change a pull request that a review signed off, such as
   to build a suggestion, `fix-pr` first runs `project review hold <number>`,
   which converts it to a draft, and runs `project review ready <number>` once
   the push lands. The hold leaves the `clean` label on, because the head is
   still the one the review signed off. A draft that carries `clean` tells a
   reader that the pull request was signed off and that its author is building
   a change, such as a suggestion, on top of it. The push removes the label.
7. Each push to a pull request that carries the `clean` label starts the
   workflow, which removes the label. In a repository without the workflow,
   `project review setup` removes it when the next review of that pull request
   starts.
8. In a repository that requires `projector/review`, GitHub blocks the merge
   until the current head has a `success` status. A new head starts with no
   status, so it waits for its own review.

An explicit `review-pr` run is a request to review now. It reviews the head it
names even on a draft, and the pull request subscription it creates announces
every later head, draft or not. Only the pull requests a repository
subscription covers wait for ready.

### 2.2 Sign-off

A head is signed off when the newest counted Projector verdict on it, from any
model, is clean and no Projector finding thread is open. That is the rule
`publish` uses for draft state today, without the hold. Draft state plays no
part in it.

| Current head | `clean` label | `projector/review` status, where required |
| --- | --- | --- |
| Signed off | Present | `success` |
| The newest verdict requests changes, or a finding thread is open | Absent | `failure` |
| No verdict yet | Absent | None, so GitHub shows the required check as expected |

The label means that Projector's newest review of the current head is clean.
It doesn't mean that the pull request may merge, because a repository can
still require people's approval.

The `reviewing` label follows the review's start comment, the comment
`setup` posts to say that a review of a head is under way. `setup` adds the
label when it posts the comment. `publish` and `finish` remove the label when
they delete the comment, but only when no other model's live start comment
is left on the pull request, so one model finishing never hides another
model's review. A review whose session dies leaves the label, as it leaves its
start comment, until the next review of the pull request finishes or `finish`
clears it.

### 2.3 What Projector writes to GitHub

| Item | Shape |
| --- | --- |
| Commit status | `POST repos/{owner}/{repo}/statuses/{sha}` on the SHA the verdict names, with `context` `projector/review`, `state` `success` or `failure`, a `description` of at most 140 characters such as `Clean: claude-opus-5-5` or `Changes requested: 2 open findings`, and `target_url` set to the review's URL |
| Labels | `clean`, color `0e8a16`, description `Projector's newest review of the current head is clean`; and `reviewing`, color `fbca04`, description `A Projector review of this pull request is in progress`. `setup` and `publish` create each label the first time a repository needs it. |
| Ruleset | Name `Projector review`, `target` `branch`, `enforcement` `active`, `conditions.ref_name.include` `["~DEFAULT_BRANCH"]`, one `required_status_checks` rule that requires the context `projector/review` with `strict_required_status_checks_policy` `false`, and no bypass actors |
| Workflow | `.github/workflows/projector-clean.yml`, below |

```yaml
name: Projector clean label

# Removes the `clean` label when a commit is pushed, because the label says
# that Projector's newest review of the current head is clean, and a new head
# has no review yet. `pull_request_target` gives the token write access on a
# pull request from a fork; the job never checks out the pull request's code.
on:
  pull_request_target:
    types: [synchronize]

permissions:
  pull-requests: write

jobs:
  unclean:
    if: contains(github.event.pull_request.labels.*.name, 'clean')
    runs-on: ubuntu-latest
    steps:
      - run: gh pr edit "$PR" --repo "$REPO" --remove-label clean
        env:
          GH_TOKEN: ${{ github.token }}
          PR: ${{ github.event.pull_request.number }}
          REPO: ${{ github.repository }}
```

`publish` decides whether the status is required by reading the rules for the
repository's default branch, `GET repos/{owner}/{repo}/rules/branches/{branch}`,
which any account that can read the repository may call. It reads the default
branch rather than the pull request's base, so a stacked pull request's head
already carries the status when its base retargets to the default branch.

`publish` keeps its order and its retry: it submits the review, re-reads the
verdicts and threads, posts the status, changes the labels, records the
review, deletes its start comment, and releases the lock. A refused status
exits non-zero and names the status, and running `publish` again posts it
without a second review, as a refused draft transition does today. A refused
label change prints a warning and exits 0, because the labels are a view and
the status is the sign-off. `setup` treats a refused `reviewing` label the
same way.

### 2.4 Commands and settings

| Command | Effect |
| --- | --- |
| `project review ready <number>` | Marks the operator's own pull request ready for review and reads the state back. It refuses a pull request someone else wrote. |
| `project review hold <number>` | Converts the operator's own pull request to a draft and reads the state back. It refuses a pull request someone else wrote, and takes none of today's `--add` or `--drop` options. |
| `project review status <number>` | Reports whether the head is signed off, the label, and the status, in place of today's draft state and hold. |
| `project init --require-review` | Answers `init`'s question yes without a prompt (§ 2.5). |
| `project init --no-require-review` | Answers it no without a prompt. |

| Key | Type | Default | Effect |
| --- | --- | --- | --- |
| `review.labels` | bool | `true` | `false` stops `setup`, `publish`, and `finish` from adding or removing the `reviewing` and `clean` labels. |
| `review.require_check` | bool | unset | Records the answer to `init`'s question. Unset, `init` asks at a terminal; `false`, it never asks; `true`, it keeps the ruleset and the workflow in place. `publish` never reads it, because the ruleset decides the status (§ 2.3). |

### 2.5 Setting up the required check

When `init` runs at a terminal in a GitHub repository whose default branch
doesn't require `projector/review`, and `review.require_check` is unset, it
asks:

```text
Does this repository rely on Projector alone to sign off pull requests before merge? [y/N]
```

Without a terminal or a flag, `init` asks nothing and changes nothing. A yes,
from the prompt or from `--require-review`, does four things in order:

1. Creates the ruleset (§ 2.3). Creating a ruleset takes admin access, and a
   private repository needs a paid plan for rulesets. On a refusal, `init`
   reports why and changes nothing else.
2. Writes the workflow, which you commit with the instructions block. It
   starts removing the label once it reaches the default branch.
3. Posts the `success` status on the current head of each open pull request
   that is signed off, so none waits on a review it already has.
4. Records `review.require_check = true` in the repository's
   `.projector.toml`.

A no records `review.require_check = false` and does nothing else. When
`review.require_check` is `true`, `init` asks nothing and creates whichever of
the ruleset and the workflow is missing. When the default branch already
requires `projector/review` and the setting is unset, `init` doesn't ask, and
it writes the workflow. `init` never
removes anything. To stop requiring the check, delete the ruleset in the
repository's settings, delete the workflow file, and set
`review.require_check` to `false`.

### 2.6 Daemon events

| Event | Today | After |
| --- | --- | --- |
| `NEW PR`, `NEW HEAD`, and the `NEW HEAD` reminder | Each head of each covered pull request | Under a repository subscription, only a head that isn't a draft. Marking a draft ready announces its head. |
| `RESPONDED` | The operator's own draft, or a pull request whose newest verdict requests changes, once the author answers | Only a pull request that isn't a draft and whose newest verdict requests changes |
| `DRAFT`, to the session that fixes the pull request | A draft that no session on this machine reviews | Replaced by `UNREVIEWED`: a pull request ready for review whose head isn't signed off, and that no session here or live start comment elsewhere is reviewing, once per head |
| The author's hold comment | Silences `RESPONDED`, and lifting it counts as an answer | Gone, because a held pull request is a draft |

The daemon still adopts drafts, so a draft's coverage and owner stay settled
while it waits, and it still writes nothing to GitHub.

### 2.7 Changes by file

| File | Change |
| --- | --- |
| `src/projector/review.py` | Add one sign-off function (§ 2.2). `publish` posts the status and changes the label in place of the draft transition, never approves a draft, and drops the hold from its approval and dismissal rules. `setup` adds `reviewing` and removes a stale `clean`. `publish` and `finish` remove `reviewing` once no live start comment is left. `status` reports the sign-off and the labels. `ready` and `hold` replace `hold_add`, `hold_drop`, `hold_end`, `hold_lines`, and `HOLD_MARKER`. |
| `src/projector/watch.py` | `route_review` announces nothing for a draft under a repository subscription and changes the `RESPONDED` rule. `route_fix` sends `UNREVIEWED` in place of `DRAFT`. `is_held` goes. |
| `src/projector/cli.py` | `review ready`, the new `review hold`, `publish`'s output, and `init`'s question and flags, beside `--site` and `--publish-rule`. |
| `src/projector/config.py` | `review.labels` and `review.require_check`. |
| `src/projector/templates/clean-workflow.yml` | New: the workflow text, with the label name filled in from the constant `publish` uses. |
| `src/projector/templates/agents-block.md` | The draft rules (§ 2.1), at version 25. |
| `src/projector/permissions.py` | Drop the docstring's note about the hold comment's deletion. |
| `hooks/github.ts`, `hooks/types.d.ts`, `hooks/register.tsx` | Read each pull request's labels. A row prints `draft`, `ready`, `reviewing`, or `clean`, and only `clean` colors it green. |
| `skills/review-pr`, `skills/review-repo`, `skills/fix-pr`, `skills/implement`, `skills/spec`, `skills/cleanup` | The rules in § 2.1. `fix-pr` keeps the heading "Hold the pull request while changes are built", which other files cite. |
| `AGENTS.md`, `CLAUDE.md` | The repository's own hand-over rules and the generated block. |
| `docs/cli.md`, `docs/plugins.md` | Living documentation for the commands, events, settings, and panel. |
| `docs/projects/watch-daemon/readme.md` | The `DRAFT` and `RESPONDED` rows of its events table, because that project is still in progress. |
| `tests/test_review.py`, `tests/test_watch.py`, `tests/test_instructions.py`, `tests/test_cli.py`, `hooks/panel.test.tsx` | § 4. |

## 3. Why this design

- **Draft, not a `ready` label or a review request.** GitHub refuses to
  request a review from a pull request's author, so the operator can't ask for
  their own review on their own pull request, which is the main case
  `review-repo` covers. A `ready` label would add a second readiness state
  beside draft that could disagree with it. Draft is what GitHub and people
  already read as "not ready for review".
- **A label and a status, not one of them.** The label is readable at a glance
  in the pull request list, but it belongs to the pull request, not to a
  commit, so it survives a push. A status belongs to one commit, so a new head
  never inherits it, and a ruleset can require it. GitHub has no rule that
  requires a label. A repository that needs one runs a workflow that posts a
  status anyway.
- **The status only where a ruleset requires it.** At most workplaces a clean
  Projector review is never a merge decision, because people still approve.
  There, a `projector/review` check would put a failing mark beside CI on each
  pull request a review asked to change, for colleagues to read as a broken
  build.
- **The ruleset, not a setting, decides the status.** With a setting, a
  ruleset that exists while the setting is off leaves every pull request
  waiting on a status nobody posts. Reading the rules has no state to drift.
- **A workflow removes the label, not the daemon.** The workflow runs on
  GitHub on every push, from anyone, whether or not a Projector session is
  running. The daemon sees a push only while a session subscribes to the pull
  request, and removing the label would be its first write to GitHub. The
  job-level `if` skips the job, so no runner starts, unless the label is
  present. `setup` removing a stale label covers a repository without the
  workflow.
- **The workflow comes with the same yes.** A workflow file changes a shared
  repository, which a workplace can't always accept. A repository that relies
  on Projector alone wants every part. A flag for the workflow alone can come
  later (§ 7).
- **The hold becomes a draft.** Draft now says what the hold comment said, that
  the author is building a change no review has read, and GitHub refuses to
  merge a draft, which the comment couldn't enforce. That removes the hold
  marker's parsing from `publish`, `status`, and the daemon, and the
  automatic-approval refusal of the comment's deletion. An older Projector's
  hold also converted the pull request to a draft, so a newer reviewer skips
  an older author's held pull request.
- **`project review ready` and `hold`, not bare `gh pr ready`.** The commands
  refuse a pull request someone else wrote, and read the state back. The
  `Bash(project *)` rule that `init` adds already allows them, while a host's
  automatic approval could refuse a bare `gh pr ready` as self-approval,
  because it was the sign-off until now.
- **A pull request subscription reviews drafts.** A person who runs `review-pr`
  asks for that review explicitly. It also gives a workplace a way to have
  Projector review a draft before people see it (§ 7).
- **No debounce.** Waiting for a quiet spell before reviewing a head would cut
  the reviews of quick pushes without any ceremony, but not of a pull request
  iterated for an hour, which is where the reviews in § 1 went. A debounce can
  be added later on top of this design.
- **`reviewing` follows the start comment.** The start comment already marks
  a review in progress, and the daemon and `setup` already read it to tell a
  live review from a stalled one. Tying the label to it means the label never
  claims more than the comment does, and needs no state of its own. One label
  serves every model, so it stays until the last live start comment goes.
- **One sign-off function.** `publish` computes draft state today from the
  newest counted verdict (`head_verdicts` and `counted_reviews`) and the open
  finding threads. That computation becomes the sign-off function, and
  `publish`, `setup`, `status`, `init`'s backfill, and the daemon's
  `RESPONDED` and `UNREVIEWED` all call it. One constant names the label for
  `publish`, `setup`, the panel, and the workflow template, and another names
  the `projector/review` context for `publish` and the ruleset.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Push to the operator's draft under `review-repo` | No `NEW PR`, `NEW HEAD`, or reminder |
| Mark that draft ready | One `NEW PR` for a pull request never reviewed, or one `NEW HEAD` from the last reviewed head |
| Convert a ready pull request to a draft while its review runs | The review publishes, and nothing more is announced until the pull request is ready again |
| Run `review-pr` on a draft | It reviews the current head, and its subscription announces later draft heads |
| Publish a clean review where the default branch requires `projector/review` | A `success` status on the verdict's SHA, the `clean` label added, and draft state unchanged |
| Publish a review that requests changes there | A `failure` status, the label removed, and draft state unchanged |
| Publish a clean review where no rule requires the check | The label only, with no status request |
| Publish a clean verdict while another model's finding thread is open | A `failure` status and no label |
| Have GitHub refuse the status | `publish` exits non-zero and names the status, and running it again posts the status without a second review |
| Have GitHub refuse the label | A warning, exit 0, and the status posted |
| Push after a clean review, in a test repository with the workflow and the ruleset | The workflow removes the label, the new head has no status, and the merge box waits for `projector/review` |
| Run `setup` on a head that isn't signed off while `clean` is present | `clean` removed and `reviewing` added |
| Publish a clean review | `reviewing` and `clean` changed in one request |
| Publish one model's review while another model's start comment is live | `reviewing` stays until the second model publishes or finishes |
| Run `finish --force` on a review in progress with no other review live | `reviewing` removed |
| Run `project review ready` or `hold` on someone else's pull request | Refused, with draft state unchanged |
| Run `init` at a terminal with `review.require_check` unset | The question. `y` creates the ruleset, writes the workflow, posts the status on signed-off heads, and records `true`. `n` records `false` and does nothing else. A second run asks nothing. |
| Run `init` without a terminal and without a flag | No question and no change |
| Run `init --require-review` without admin access | The refusal reported, and no workflow, status, or setting written |
| Resolve every thread on a ready pull request whose newest verdict requests changes | One `RESPONDED`; on a draft, none |
| Leave a ready pull request whose head isn't signed off with no reviewing session | One `UNREVIEWED` for the head; for a draft, none |
| Open the panel on a pull request that carries the label | Its row prints `clean` in green |

Each of these tests fails before the change:

- **`test_watch.py`: a draft head under a repository subscription is not
  announced.** It catches `route_review` announcing a draft head, which is
  today's behavior.
- **`test_watch.py`: marking a draft ready announces its head once.** It
  catches a gate that forgets the head, so the pull request never gets
  reviewed.
- **`test_watch.py`: a pull request subscription announces a draft head.** It
  catches a gate applied to explicit requests.
- **`test_review.py`: a clean publish posts `success` on the verdict's SHA.**
  The fake GitHub moves the pull request's head between the submit and the
  status, and the test asserts the status path names the reviewed SHA. It
  catches a status posted on whatever the head is at the time.
- **`test_review.py`: `publish` never runs `gh pr ready`.** It catches a
  leftover draft transition.
- **`test_review.py`: an open finding thread keeps the label off and the status
  failing.** It catches a sign-off that reads the verdict alone.
- **`test_review.py`: the first model to publish leaves `reviewing` while a
  second model's start comment is live.** It catches a label removed on the
  publishing review's own comment alone.
- **`test_review.py`: `setup` removes a stale label.** It catches a label left
  on a head no review has read, in a repository without the workflow.
- **`test_cli.py`: `init`'s question, flags, and refusal, in a temporary Git
  repository with a fake `gh`.** It catches a prompt without a terminal and a
  partial setup after a refused ruleset.
- **`test_cli.py`: the workflow `init` writes removes the label `publish`
  adds, and the ruleset `init` creates requires the context `publish` posts.**
  It pins each value through what depends on it, so renaming one side turns
  the test red.
- **`test_instructions.py`: the template text is pinned to version 25.**
- **`panel.test.tsx`: a labeled pull request's row prints `clean` in green, and
  a ready one without the label prints `ready` in yellow.**

The tests that assert today's draft transitions, the hold comment, and the
`DRAFT` event change to assert the label, the status, and `UNREVIEWED`, or go
with the code they cover.

## 5. Cost

- **Reviews.** They are the cost this project cuts: each draft head skipped
  saves a full read of the change and a run of the validation gate. Measure
  Projector verdicts per merged pull request before and after, in a repository
  whose pull requests are iterated after they open.
- **`setup` and `publish`.** `setup` makes one more REST request per review,
  adding `reviewing`. `publish` makes up to three more per verdict: the branch
  rules, the status, and one label change. Each label's creation adds one
  request, once per repository. They are small next to the review.
- **The daemon.** It makes no new requests. Its query already reads `isDraft`,
  and marking a pull request ready changes the ETag the daemon already
  watches.
- **The workflow.** It runs on each push to a pull request in a repository that
  has it. The job is skipped, and no runner starts, unless the label is
  present.
- **The panel.** Its query reads each pull request's labels, one connection per
  pull request.
- **The conversation.** Each review adds two label events to the pull
  request's conversation: `setup` adding `reviewing`, and `publish`'s one
  change of both labels.
- **The summary comment.** A label event shows on the conversation, so it
  buries the summary comment, which the next summary publish then posts again.
  `setup` adds `reviewing` at the start of a review whose verdict buries the
  comment anyway, `publish` changes the labels before the summary is
  published, and the workflow removes `clean` on a push, which buries the
  comment anyway. So the labels cause no extra reposts.

## 6. Rollout

1. Ship the daemon, `publish`, the commands, the skills, the template, and the
   panel in one release. An older daemon with the newer skills reviews every
   head of a draft the agent is still working on. A newer daemon with the
   older skills never reviews a pull request that waits as a draft for a
   sign-off that no longer comes.
2. After you upgrade, mark ready each of your open drafts that waits on a
   review. Those drafts meant "not signed off", and the daemon now waits for
   them. `gh pr list --author <login> --draft` lists them, and
   `project review ready <number>` marks each one.
3. Run `project init` in each repository to refresh the instructions block. In
   a repository that relies on Projector alone, answer yes, or pass
   `--require-review`, as an admin, then commit the workflow it writes.

A ready pull request signed off before the release carries no label until its
next review.

## 7. Open questions

- **People are asked for review when the agent marks a pull request ready.** In
  a workplace repository, code owners and requested reviewers can see a pull
  request before Projector's first review of it. Deferred until a workplace
  repository shows the cost. The remedy is in place: the agent can keep the
  pull request a draft, run `review-pr` on it, and mark it ready after a clean
  verdict.
- **Classic branch protection.** The rules endpoint reports rulesets only, so a
  repository that requires `projector/review` through classic branch
  protection gets no status. Deferred, because `init` creates a ruleset.
- **A `pending` status while a review runs.** Deferred. GitHub already shows a
  required check that has no status as expected.
- **A flag for the workflow alone.** Deferred until a repository wants the
  workflow without the required check.

## 8. Background

- **Review counts in this repository.** The last 40 merged pull requests before
  this plan got 71 Projector verdicts, a median of 2 and at most 5.
- **The repository in § 1.** Its pull request with 9 reviews had 5 clean
  verdicts. Three came in a row, 6 and 17 minutes apart, each followed by commits
  that changed the layout, its icons, or its sizes, not by fixes for a
  finding.
- **GitHub's rules.** GitHub refuses a review request naming the pull request's
  author (`Review cannot be requested from pull request author`). It refuses
  to merge a draft. It keeps the newest status for each context on each
  commit. A ruleset's required check accepts a status from any account that
  can write to the repository unless the rule names an app. Rulesets need a
  paid plan in a private repository.
- **Where draft state is written today.** `publish` runs `gh pr ready` or
  `gh pr ready --undo` in `review.py`, and `hold --add` converts the pull
  request to a draft. The daemon reads `isDraft` for the `DRAFT` event in
  `route_fix` and for the "still a draft" `RESPONDED` in `route_review`.
  Projector writes no commit status and no label anywhere.
- **The label-check pattern.** A repository that requires a label before merge
  runs a workflow on `pull_request_target` that reads the labels and posts a
  commit status, which a ruleset requires. That workflow also has to cope with
  stacked pull requests whose base predates it. A status that `publish` posts
  itself needs no such workflow.
