---
status: in-progress
priority: now
owner: ninjudd
---

# Ship the review's mechanical steps as commands

## 1. Outcome

`review-changes` does its mechanical work through `project review` commands
that ship with Projector, and keeps its prose for the judgment: what to flag,
how to verify, and what to write. A review loop no longer rebuilds helpers in
a job directory that is cleaned up behind it, and the mistakes those helpers
kept making are refused by code that is tested once.

## 2. Problem

`start-review-loop` ships one script, `watch-prs.sh`. Everything else a review
does is prose in `review-changes/SKILL.md` with inline `gh` snippets, so each
loop session rebuilds the same helpers, and they vanish when its job directory
is cleaned up. The starkey loop rebuilt them several times after 2026-09-19,
and its record shows the hand-built versions failing the same ways:

- The duration snippet used `datetime.UTC`, which older Python lacks, so a
  review published with `took 0m 00s` and an empty `seconds=`.
- Two hand-typed 40-character SHAs in one publish were both wrong; only a log
  match on the full SHA stopped it.
- A review went out as a signature line with no body, because a helper did not
  check that the composed file existed.
- A body kept a stale `took` from an earlier head while its marker carried the
  new `seconds=`.
- An anchor computed with `grep` came back empty, so a thread cited `path:`
  with no line.
- Two waiting instances published the same review twice, with six duplicate
  threads.
- A publish helper run outside the checkout failed at `gh pr view` and exited 0.

None of these needs judgment. Each is a check a command can make every time.

## 3. Acceptance criteria

- `project review setup`, `move`, `census`, and `publish` exist, are
  documented in `docs/cli.md`, and are what `review-changes/SKILL.md` tells
  the reviewer to run in place of its inline `gh` snippets.
- Each failure in § 2 has a test that reproduces it against the command and
  shows the command refusing, with a non-zero exit, or doing it right.
- A review published through the commands carries a body, a duration and
  `seconds=` computed from the start comment at publish time, the head's full
  SHA from GitHub rather than from an argument, and only threads anchored to
  lines inside the pull request's diff.
- A second instance for the same pull request and head exits without posting.
- `review.gate`, when set, names the command the review runs as its
  repository's validation gate.

## 4. Design

### 4.1 Commands in the CLI, not scripts beside the skill

The proposal this plan comes from put four shell scripts under
`review-changes/scripts/`. They belong in the `project` CLI instead, as
`project review <step>`:

- Several failures in § 2 are portability failures: date arithmetic, a Python
  version, a helper run from the wrong directory. The CLI is standard-library
  Python 3.11 or newer on every host Projector supports, and `date` and `jq`
  differ between GNU and BSD.
- The CLI already talks to GitHub for `project summary publish` and
  `project site status`, and `review-changes` already calls both, so the skill
  gains no new dependency.
- The commands are tested with `unittest` and a fake `gh`, the way the site
  and summary commands are, rather than through a shell harness.

The watchers stay shell scripts: they run under a host's monitor as long-lived
processes and parse nothing beyond `gh` output.

### 4.2 The commands

**`project review setup <pr>`** checks that the pull request is open and comes
from the same repository, fetches its head, creates a scratch worktree at the
exact SHA GitHub reports and verifies the worktree's `HEAD`, computes the merge
base, posts the start comment as the reviewer, and writes the review's state
file: the SHA, the base, the worktree path, the start comment's id and
`created_at`, and the number of heads seen.

**`project review move <pr>`** handles a head that moved mid-review, the case
`SKILL.md` § One start comment per review describes. It creates a worktree for
the new SHA, edits the start comment in place to name it, re-reads the comment
to confirm the edit, and updates the state file.

**`project review census <pr>`** runs the paginated thread query, keeps the
threads whose first comment carries `projector-finding`, and prints the open,
resolved, and total counts with each open thread's `path:line`. It is the
count § Verify earlier findings rests on, so a review runs it after settling
the earlier findings.

**`project review publish <pr> --verdict … --body <file> [--threads <file>]`**
refuses, with a non-zero exit and the reason, unless:

- the pull request is open and its head is the SHA in the state file;
- no review this loop did not record already carries a verdict on that SHA,
  the collision check `SKILL.md` describes, read from the loop's record;
- every thread's `path` and `line` fall inside the pull request's diff hunks,
  and its text opens with the priority header and carries a `**Fix:**` line;
- for `approved`, the census shows no open finding and no thread is posted.

It then fills the body template's placeholders — the duration and `seconds=`
from the start comment's `created_at` to now in UTC, the full SHA, and the
census line — and refuses a body with a placeholder left, a signature line or
marker that does not match `SKILL.md` exactly, or nothing below the signature
line. It submits one review, re-reads the pull request's state and the review
it posted, appends the review id to the loop's record, and deletes the start
comment.

### 4.3 Shared rules

- Every SHA comes from GitHub or the state file, never from an argument the
  agent types.
- Every command takes the repository from the worktree's `origin` or `--repo`
  and passes it to `gh` explicitly, so it runs from any directory.
- A command that posts runs `gh` as the reviewer by setting `GH_TOKEN` to
  `gh auth token --user <reviewer>` for that call alone, never by switching
  the global account, so a session that pushes as the operator is unaffected.
  The reviewer comes from `review.username` or the authenticated user, as
  `SKILL.md` § Resolve identity says.
- `setup` and `publish` take a lock per pull request and SHA, created
  exclusively in the state directory, so a second instance exits.
- The state directory is `$XDG_STATE_HOME/projector/reviews`, falling back to
  `~/.local/state/projector/reviews`, with one file per repository and pull
  request, and `--state-dir` overrides it. The loop's record of published
  review ids lives beside it, replacing the record `start-review-loop` keeps
  in its own job directory.
- Every refusal exits non-zero and says what to do about it.

### 4.4 `review.gate`

The validation gate is the repository's own: formatting, linting, tests, docs,
`git diff --check`, `project check`. A `review.gate` key in `.projector.toml`
names a shell command that takes the worktree and the merge base, and
`project review gate <pr>` runs it in the scratch worktree and reports its exit
status. It runs the head's code, so it runs only when the head is trusted by
the rule `SKILL.md` § Run a head's code only when the head is trusted already
applies, and a review of an untrusted head skips it and says so.

### 4.5 The skill

`review-changes/SKILL.md` replaces each inline `gh` snippet with the command
that does it and keeps the rules those snippets served as the commands'
documented behavior. Its prose shrinks to identity, the two review modes,
verifying findings, what to flag, and how to write a finding.
`start-review-loop` hands its record to the commands instead of keeping one.

## 5. Delivery

Each step is a pull request, stacked where one needs the last:

1. This plan.
2. `project review setup`, `move`, and `census`, with the state directory, the
   lock, the reviewer identity rule, their tests, and the `SKILL.md` edits for
   the steps they take over.
3. `project review publish`, with its tests, including one per failure in § 2,
   and the `SKILL.md` and `start-review-loop` edits for publishing and the
   record.
4. `review.gate` and `project review gate`.

## 6. Decisions

- **Commands, not scripts**, for the reasons in § 4.1.
- **Built fresh.** The reference versions the starkey loop wrote on
  2026-09-27 have not published a review and live on another machine; the
  failures in § 2 are the specification, and each becomes a test.
- **After #125.** The census counts findings after the reviewer settles them,
  which is how #125 defines a clean head, so the commands encode that model
  rather than the one before it.
