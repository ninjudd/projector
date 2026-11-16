---
status: ready
priority: now
---

# Run the full gate once per head, in CI

A repository that names its CI checks runs its full test suite once for each
head, in CI, and the author, the fixer, and the reviewer use that result
instead of running the suite again.

## 1. Problem

Projector's skills tell three parties to run a repository's full validation
gate on the same head:

1. **The author or fixer.** `implement` ("Verify the current slice") and
   `fix-pr` (step 2 of the fix, and every layer a stack rebase rewrites) run
   the full gate before each push, so a push never carries a known failure.
2. **CI.** The repository's workflow runs the suite on every push, as
   Projector's own `Check` workflow does.
3. **The reviewer.** A full review runs `project review gate` when
   `.projector.toml` sets `review.gate`, and otherwise "the checks the
   repository's instructions name", which is the whole gate again. An
   incremental review runs it once more whenever the merge base moved.

Each run checks the same code, so two of the three are waste, and the waste
grows with every fix-and-review cycle. In Projector, the suite took about nine
minutes before it was cut to about one. A pull request that went through four
review cycles ran it up to twelve times, where four runs in CI would have
given the same evidence. The rules live in the shared skills, so every
repository that uses Projector pays this cost.

## 2. Solution

A repository names, in a new `review.gate_checks` setting, the CI checks that
run its suite. Where the setting is set, the full suite runs only in CI:

- **The author and the fixer.** Before a push, they run the tests that cover
  what they changed, plus the checks `review.gate` names, and not the full
  suite.
- **The reviewer.** `project review gate` waits for the named checks on the
  head and records their result in the same gate record a local run writes.
- **The watch.** A new `CHECKS` event tells the fixer when CI fails on the
  head, so a failure the author did not run for still comes back to be fixed.

`review.gate` keeps its job: the part of the gate CI cannot run, which Projector
itself needs, because `hooks/typecheck.sh` cannot run in CI. A repository
without `review.gate_checks` keeps today's behavior.

### 2.1 Flow after the change

1. The author changes code, runs the tests for what changed and the
   `review.gate` command, and pushes. CI starts on the new head.
2. The reviewer sets up the head and reads it by the method. When it reaches
   the gate, it runs `project review gate <number>`.
3. `gate` checks trust first, as today. Then it checks whether the head
   changed any file under `.github/workflows/` since the merge base. Such a
   head defined what CI ran, so its CI result is not evidence about its code.
   For that head, `gate` refuses to use CI and tells the reviewer to run the
   repository's full gate locally.
4. Otherwise, `gate` runs the `review.gate` command when it is set. Then it
   polls the head's check runs every 20 seconds, touching the review's lock as
   a long local gate does today, until every named check has a completed run
   or 30 minutes pass.
5. `gate` writes the gate record (§ 2.2) and exits:
   - **0** when the command exited 0 and every named check concluded
     `success`.
   - **1** when the command failed, or when any named check concluded
     otherwise.
   - **75** when a named check had no completed run within 30 minutes.
6. The reviewer treats exit 1 as a failing gate, as today. On exit 75 it
   reviews by reading, as it does an untrusted head, and says in the review's
   disclosures that CI had not finished. The record's `exit_code` is not 0, so
   the incremental rule (`incremental-review` § 2.2.1) still demands a passing
   gate across a moved base.
7. Independently of any review, the watch daemon reads the head's CI rollup
   on each poll. When the rollup is `FAILURE` or `ERROR`, it sends the `fix`
   role one `CHECKS` line for that head. `fix-pr` then reads the failing run's
   log, reproduces the failure, and fixes it, or reports why it cannot.

### 2.2 Data

`review.gate_checks` in `.projector.toml`:

| Field | Value |
| --- | --- |
| Type | String: check names separated by commas, as `check` or `test, lint`. |
| Default | Unset: everything runs locally, as today. |
| Matches | A check run whose `name` equals one of the names and whose `app.slug` is `github-actions`. Only the latest run of each name counts. |

The gate record in the review's `state.json` gains two fields. It keeps the
four it has, so `gate_passed` reads it unchanged.

| Field | Value |
| --- | --- |
| `command` | The `review.gate` command, or `null` when only CI ran. |
| `exit_code` | 0, 1, or 75, as § 2.1 step 5 defines. |
| `sha` | The head the gate covers. |
| `ran_at` | When the gate started. |
| `checks` | New. One `{"name", "conclusion", "url"}` per named check, with `conclusion` `null` for a check still running at the timeout. Absent when `review.gate_checks` is unset. |
| `source` | New. `ci` when `checks` decided the result, and `local` otherwise. |

The `CHECKS` event, sent once per head and repeated after `watch.renotify` as
`CONFLICT` is:

```text
CHECKS acme/app#12 head=1a2b3c4d failed: check, lint — <action>
CHECKS acme/app#12 (still open) head=1a2b3c4d failed: check, lint — <action>
```

### 2.3 Changes by file

| File | Change |
| --- | --- |
| `src/projector/config.py` | `review.gate_checks: str` in `SETTINGS`. |
| `src/projector/review.py` | `gate` takes the parsed names. It checks the head for changed workflow files with `git diff --name-only <base> <sha> -- .github/workflows`, runs the command when set, then calls a new `ci_checks(repo, sha, names)`. That function reads `gh api repos/<repo>/commits/<sha>/check-runs?filter=latest` and keeps the `github-actions` runs it names. `gate` waits in a loop that reuses `touch_head`. |
| `src/projector/cli.py` | `review gate` reads `review.gate_checks`, prints each check's conclusion, and exits as § 2.1 step 5 defines. |
| `src/projector/watch.py` | `QUERY` reads `statusCheckRollup{ state contexts(first:20){ nodes{ … on CheckRun{ name conclusion } … on StatusContext{ context state } } } }`. The fix events add `CHECKS` through `standing`, keyed `checks:<number>:<head>`. |
| `skills/review-pr/SKILL.md` | The gate paragraph under "Run a head's code only when the head is trusted" says that with `review.gate_checks` set, `gate` waits for CI, and how to handle exits 1 and 75 and a changed workflow. |
| `skills/implement/SKILL.md` | "Verify the current slice", and the stack rebase rule, say: where `review.gate_checks` is set, run the tests that cover the change and the `review.gate` command, not the full suite, because CI runs it on the push. |
| `skills/fix-pr/SKILL.md` | Step 2 and the stack layers take the same rule. A new kind of work handles a `CHECKS` line: read the failing job's log with `gh run view <id> --log-failed`, reproduce the failure locally with the failing test, then fix it. A failure the fixer cannot reproduce, or one in infrastructure the change did not touch, is reported, not patched. |
| `skills/finish/SKILL.md` | Step 4 accepts the named checks passing on the head as the complete gate's evidence. |
| `docs/cli.md` | The settings table gains `review.gate_checks`. The `review gate` section describes the CI wait, the workflow-file refusal, and the exit codes. |
| `docs/plugins.md` | The watch events it lists gain `CHECKS` beside `CONFLICT`. |
| `.projector.toml`, `AGENTS.md` | Projector sets `review.gate_checks = "check"`. `review.gate` names the checks CI cannot run: the two `claude plugin validate` runs, `claude plugin test .`, `hooks/typecheck.sh`, and `git diff --check`. `AGENTS.md` says which part of the gate CI runs. |

## 3. Why this design

- **One setting names what CI covers.** The author, the fixer, the reviewer,
  and `finish` all need the same fact: whether CI runs the suite. Each reads
  `review.gate_checks`, so no skill infers it from the workflow files, and a
  repository opts in once.
- **The gate record is reused, not duplicated.** A CI result lands in the
  `gate` record a local run writes, so `gate_passed` and the incremental gate
  rule need no change. The `incremental-review` plan rejected relying on CI
  because "a verdict does not wait for CI and many repositories have none".
  Here `gate` waits for CI, and a repository without CI never sets the key.
- **`review.gate` keeps its meaning.** It still names a command the reviewer
  runs locally. With `gate_checks` set, that command shrinks to the part CI
  cannot run, so one key does not change meaning depending on another.
- **Only GitHub Actions runs on the head count.** Anyone with write access
  can post a commit status, but a check run from the `github-actions` app ran
  the repository's workflow on that commit. The gate runs only on a trusted
  head, as today. A `pull_request` workflow runs the workflow file from the
  pull request itself, so a head that edits `.github/workflows/` could make
  CI pass without testing anything, which is why such a head runs the gate
  locally.
- **The author runs focused tests, not nothing.** A push that breaks a test
  for the code it touched wastes a CI run and a review cycle. The tests
  nearest the change catch most of those for a fraction of the suite's cost,
  and the `CHECKS` event catches the rest.
- **`CHECKS` fires in every repository.** The rollup is already fetched for
  the panel, and a red CI run on a pull request the session fixes is work for
  the fixer, whether or not the repository sets `gate_checks`. `fix-pr`
  verifies a failure before it patches anything, as it does a finding, so a
  flaky or unrelated check gets a report instead of a change.
- **Rejected: skip the reviewer's gate when CI is green, without waiting.** A
  review often reaches the gate before CI finishes. Without a wait, the
  reviewer would either run the suite anyway or sign off with no evidence.
- **Rejected: the reviewer reads the rollup instead of check runs.** The
  rollup mixes every check and status, including ones that do not run the
  suite, such as deploy previews. It cannot say which run covered the gate.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Run `project review gate` on a trusted head, with `review.gate_checks = "check"` and a `github-actions` run `check` concluding `success` | Exit 0, a record with `source: ci` and one check, and no local suite run. |
| The same, with the run concluding `failure` | Exit 1, the record names the check and its URL, and `gate_passed` is false. |
| The same, with the run still `in_progress` past the wait | Exit 75, `conclusion: null`, and `gate_passed` is false. The test shrinks the wait rather than taking 30 minutes. |
| A `check` status posted by another app, or a commit status named `check` | It is ignored, and the gate waits for a `github-actions` run. |
| A head that changes `.github/workflows/check.yml` | `gate` refuses to use CI and names the changed file. |
| `review.gate` set beside `gate_checks`, with the command failing | Exit 1, whatever CI concluded. |
| `review.gate_checks` unset | `gate` behaves exactly as today. |
| An incremental review across a moved base, with a CI-passed gate record | `interdiff` accepts it as a passing gate. |
| The watch polls a head whose rollup is `FAILURE` | One `CHECKS` line for the `fix` role, naming the failed checks, and no repeat before `watch.renotify`. |
| A new head after a `CHECKS` line | No `CHECKS` line until that head's own rollup fails. |

These tests fail before the change:

- **The `github-actions` app filter.** A test plants a passing check run from
  another app. It catches a gate that trusts any check named `check`.
- **The workflow-file refusal.** It catches a gate that lets a head vouch for
  itself.
- **The timeout.** It catches a gate that reads a pending check as passed, or
  that never returns.
- **The `CHECKS` key.** It is keyed per head, which catches an event that
  repeats on every poll or survives a new head.

The skill changes are prose, which a review reads against this plan. No test
pins their wording.

## 5. Cost

- **The suite.** It runs once per head, in CI, instead of up to three times.
  Projector's gate now takes about a minute locally, and CI runs it on a
  4-core runner.
- **The reviewer's wait.** It replaces a local run, so a review's wall time
  stays about the same, and the reviewer's machine spends no CPU on the suite.
  `gate` makes at most 90 REST calls per run, one every 20 seconds for 30
  minutes, against GitHub's 5,000 an hour.
- **The watch query.** It reads up to 20 rollup contexts per pull request.
  That adds a few points to the GraphQL cost the query already reports in
  `rateLimit`. The implementation measures that cost on a pull request with
  CI and records it here.

## 6. Open questions

None blocks implementation.

- **A configurable wait.** The 30-minute wait is a constant. Add a setting
  only when a repository's CI routinely takes longer. Deferred.
- **Checks that run on a schedule or in a merge queue.** `gate_checks` covers
  checks that run on each pushed head. A repository whose suite runs only in a
  merge queue leaves the setting unset. Deferred.
