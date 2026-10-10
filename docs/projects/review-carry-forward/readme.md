---
status: ready
priority: next
---

# Carry a clean review across a minimal push

When a push changes a cleanly reviewed pull request only a little, the review
loop reads that change, judges it minimal, and publishes a short clean verdict
that names the head it carried from, in place of a full review.

## 1. Problem

The review loop (`start-review-loop`) reviews every head pushed to the
operator's pull requests. For each head a subagent runs `review-pr`: it sets
the head up in a scratch worktree, reads every changed file through the passes
in `method.md`, runs the repository's validation gate on a trusted head,
settles earlier findings, and publishes a review whose body restates the
intent, the census, the coverage, and what it checked. The skill requires that
full run for every pushed SHA, including a fix-only one, so that a verdict
always names the head it judged. Draft state, the summary page's header, and
the fix loop all read the verdict on the current head.

Nothing in that flow tells a substantial push from a trivial one. On pull
request #209 in this repository, the loop published a clean review at
`a8cf3d5`. The next push, `ad1e106`, changed the side padding of two sidebar
rows in `site/assets/summary.css` from 8px to 9px. The loop gave it the same
full run as every other head: 514 unit tests, the site's type check and build,
both plugin validations, and a review body of about 580 words that restated the
pull request's intent and the earlier checks. Eight of that pull request's 18
verdicts came in two runs of pushes that changed only
`site/assets/summary.css`, each run adding up to 15 lines or fewer. Those
eight reviews took 638 seconds, and each one gave a reader a full body to scan
for the few lines that changed.

## 2. Solution

After `project review setup`, the reviewing subagent runs
`project review interdiff <number>`. The command checks that the reviewer's
newest verdict is clean, finds the fully reviewed head that verdict rests on,
and measures the pull request's own change since that head, leaving out what a
merge of the base branch brought in. It also checks the other rules that need
no judgment: the head is trusted, no finding is open, the earlier head is an
ancestor, and the change edits files in place and stays within
`review.carry_max_lines`. When those rules pass, the subagent
reads the change and carries the verdict only when every hunk is presentation,
prose, or formatting. It then runs
`project review publish <number> --carry --change "<what changed>"`, which
checks the same rules again and posts a clean review whose body is the
signature line, the markers, and one sentence: what changed since the earlier
head, and that no re-review was necessary. Any other outcome is a full review,
as today. A carried verdict is a clean verdict on the new head with the
existing review marker, plus one marker line that names the head it carried
from, so everything that reads a verdict keeps working.

### 2.1 The flow on a new head

1. The subagent runs `project review setup <number>` as today. Setup fetches
   the head, creates the scratch worktree, records whether the head is trusted,
   takes the lock, and posts the start comment.
2. The subagent runs `project review interdiff <number>`. The command reads the
   review's state, applies the rules in § 2.2.1, and exits 0 when they allow a
   carry. It prints the head it would carry from, the counts, and the
   interdiff's patch. It exits 1 when a rule fails, naming the rule, and still
   prints the patch when it got as far as measuring one. Any non-zero exit
   means a full review. That includes the exit from a CLI too old to know the
   command, so a skill newer than the installed CLI falls back to today's
   behavior.
3. On exit 0, the subagent reads the whole patch, and the code around a hunk at
   the head when it needs context. It carries only when every hunk is one of
   the kinds in § 2.2.2. Otherwise it continues at step 5 of `review-pr`, the
   full method, on the setup from step 1.
4. To carry, the subagent runs
   `project review publish <number> --carry --change "<what changed>"`.
   Publish checks the lock and the head as it does today, applies the carry
   rules again to the live state, runs the collision check, composes the body
   in § 2.4, and posts it as a `COMMENT`. It then sets draft state, re-reads
   the review, records it in the loop's record, deletes the start comment, and
   releases the lock. A refused carry keeps the lock, so the subagent can run
   the full review on the same setup. When the head moved, the subagent runs
   `project review move <number>` and starts again at step 2.
5. The subagent updates the summary for the new head, as § 2.7 says. It
   reports the SHA, the clean verdict, the review id, no threads, and the head
   it carried from.

### 2.2 What may carry

#### 2.2.1 The rules the command checks

`project review interdiff` and `publish --carry` share one function for these
rules. It refuses at the first rule that fails:

| Rule | Refused when |
| --- | --- |
| Carrying is on | `review.carry_max_lines` is `0`. |
| The head is trusted | `setup` recorded the head as untrusted, by the rule `review-pr` uses to decide whether a head's code runs. |
| A clean verdict precedes it | The reviewer's newest Projector verdict on the pull request requests changes, names this same head, or does not exist. |
| The earlier head is an ancestor | `git merge-base --is-ancestor <earlier> <head>` fails, as it does after a rebase or a force-push. |
| No finding is open | The census counts an open Projector finding thread. |
| The change can be measured | The measurement in § 2.3 meets a conflict, or needs a Git older than 2.40. |
| Files change in place | The interdiff adds, deletes, or renames a file, changes a file's mode, or touches a binary file, a symlink, or a submodule. |
| The change is small | The interdiff's insertions plus deletions exceed `review.carry_max_lines`. |

The earlier head is the one a full review covered. When the reviewer's newest
verdict is itself carried, the earlier head is the one its carry marker names
in `from=`, so the change is measured from the last full review, and every
carried verdict names a fully reviewed head.

#### 2.2.2 The kinds of change a reviewer carries

The command bounds the size and shape of the change. The reviewer judges its
content, and carries only when every hunk of the interdiff is one of these:

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

Run the full review when any hunk is something else, when a hunk reworks code
an earlier finding thread was about, or when you are unsure. A needless full
review costs a minute or two. A wrong carry signs off a change that no pass
read.

### 2.3 Measuring the pull request's own change

The interdiff is what the pull request's diff against its base gained or lost
between the earlier head `O` and the new head `N`. A plain `git diff O N` also
counts everything a merge of the base branch brought in.

1. `MN` is the new head's merge base, which `setup` already records as `base`
   in the review's state.
2. `MO` is `git merge-base O MN`. Because `N` contains `O`, this is the merge
   base `O` has with the base branch.
3. When `MO` equals `MN`, the base did not move, and the comparison tree `T`
   is `O`'s.
4. Otherwise `T` is the tree that
   `git merge-tree --write-tree --merge-base=MO MN O` writes: the earlier
   head's change applied to the new merge base. Exit status 1 means the merge
   conflicts, which says the author resolved a conflict, so the rule refuses. A
   Git before 2.40 does not know `--merge-base`, and the rule refuses rather
   than miscount.
5. The interdiff is `git diff --no-renames T N`. Its `--numstat` gives the
   insertions, deletions, and files, and its `--raw` gives each file's status
   and modes for the in-place rule.

`merge-tree` writes objects to the checkout's object store and touches neither
the working tree nor the index, so the operator's uncommitted work stays as it
is.

With `--json`, `interdiff` prints one object, with the same exit status:

| Field | Value |
| --- | --- |
| `carry` | `true` when every rule in § 2.2.1 passes. |
| `reason` | The rule that failed, in words, or `null`. |
| `from` | The full SHA of the earlier head, or `null` when there is none. |
| `review` | The URL of that head's full review, or `null`. |
| `insertions`, `deletions` | The interdiff's line counts, or `null` when it was not measured. |
| `files` | The paths the interdiff touches, empty when it was not measured. |
| `max_lines` | `review.carry_max_lines` as read. |
| `patch` | The interdiff, or `""` when it was not measured. |

### 2.4 The carried review

Publish writes the whole body. The subagent supplies only `--change`, a phrase
that says what changed, so the earlier head the sentence names, the counts,
and the link always agree with the markers. For the push in § 1 the review
reads:

```
📽️ **Projector review** · `0.7.2` · model `claude-opus-5-5` · effort `high` · **CLEAN** · carried from `a8cf3d5` · took 0m 38s

<!-- projector-review v=1 verdict=clean projector=0.7.2 model=claude-opus-5-5 effort=high sha=ad1e1065bd69f6d819b16cc6ebe3330d63042b52 findings=0 seconds=38 covered=1/1 -->
<!-- projector-carry v=1 from=a8cf3d521b8ec3d6b430e4b59fa2b7415473e92e insertions=2 deletions=2 files=1 -->

Changes since [`a8cf3d5`](https://github.com/ninjudd/projector/pull/209#pullrequestreview-5479885726) were minimal (side padding 8px → 9px on two sidebar rows; +2 −2 lines in 1 file); no re-review was necessary.
```

The body contains, in this order:

1. **The signature line**, with `carried from <short-sha>` between the verdict
   word and the duration. The duration runs from the start comment, as today.
2. **The review marker**, in exactly today's grammar. `verdict=clean`,
   `findings=0`, and `covered=<n>/<n>`, where `n` is the number of files the
   interdiff touches, the files this verdict read.
3. **The carry marker**, on the line after the review marker, with the fields
   in the table below.
4. **One sentence**: "Changes since `<earlier short SHA>` were minimal
   (`<--change>`; +`<insertions>` −`<deletions>` lines in `<files>` file or
   files); no re-review was necessary." The short SHA links to the earlier
   head's full review. On another author's pull request, a second sentence
   follows: "A human approval is what remains."

The body leaves out what the earlier full review already says and what a carry
does not do: the intent paragraph, the census, the coverage line, disclosures,
`Suggestions`, and what was checked. The census still decides the verdict,
because publish refuses a carry while any finding is open, but it does not
print.

| Carry marker field | Value |
| --- | --- |
| `v` | `1` |
| `from` | The full SHA of the fully reviewed head the verdict carries from, the head the sentence links. |
| `insertions`, `deletions` | The interdiff's line counts. |
| `files` | The number of files the interdiff touches. |

`--change` must be one line of at most 200 characters, with no `<!--`.
`--carry` refuses `--threads`, `--body`, `--covered`, `--second-verdict`, and
`--verdict changes-requested`. Without `--carry`, `--verdict`, `--body`, and
`--covered` stay required. A carried review is always a `COMMENT`, even where
`review.allow_approve` is `true`. On a self-review, publish then marks the
pull request ready, as for any clean verdict. An ordinary review body that
holds a `<!-- projector-carry` line is refused, as one that holds its own
review marker is today.

### 2.5 Configuration

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `review.carry_max_lines` | non-negative integer | `20` | `project review interdiff` and `project review publish --carry`, as the most insertions plus deletions the pull request's own change since its last full review can have and still carry. `0` turns carrying off. |

Both commands read the key from the review's checkout, as `publish` reads
`review.allow_approve`. A value that is not a non-negative integer, such as a
string, a negative number, or `true`, makes both commands refuse and name the
key.

### 2.6 What reads a carried verdict

| Reader | What it reads | With a carried verdict |
| --- | --- | --- |
| Draft state | `publish` marks a self-reviewed pull request ready on a clean verdict. | Marks it ready by the same path. |
| Collision check | The reviewer's reviews whose body matches `projector-review .* sha=<head>`. | Matches, because the marker's `sha=` is the new head. A carried verdict collides and is recorded like any verdict, and `setup --rereview` still allows a re-review. |
| Publish's read-back | `sha=<head>` in the posted body. | Unchanged. |
| Leftover start comments | A Projector review on the comment's `sha=` submitted after it. | Unchanged. |
| Page header | `review_statuses` matches each body line against the anchored `MARKER` pattern. | Matches the unchanged marker, also reads the carry line, and adds `carriedFrom`. The header shows **Clean (carried from a8cf3d5)**, linked to the carried review. A site built by an earlier release matches the same marker line and shows plain **Clean**. |
| Fix loop watcher | A `REVIEW` event for each `COMMENTED` review with a body. | Announces the carried review once, like any clean review. `fix-pr` reads `CLEAN` and finds nothing outstanding. |
| `fix-pr`'s clean test | "A clean verdict names its current head." | Holds. |
| Review loop watcher | Heads and threads only. | Unchanged. |

### 2.7 The summary

After a carried verdict, the subagent updates the summary for the new head as
it does after any review, through the update path in `summarize-pr`, "Update
the page when the pull request moves." On a small interdiff the update is
mostly a new `pr.head` and a note moved or rewritten on a changed line. The
site then shows the new head's diff, and the header of its page shows the
carried verdict.

### 2.8 Files that change

| File | Change |
| --- | --- |
| `src/projector/review.py` | `CARRY`, the carry marker's pattern beside `MARKER`, and `verdict_marker(body)`, which returns a body's verdict, head, and carried-from head for both the carry rules and the site. `reviewer_reviews`, one paginated listing of the reviewer's Projector reviews with id, URL, time, and body: `check_collision` filters it by `sha=` as today, and the carry rules read the newest verdict from it. `interdiff` (§ 2.3) and `carry_rules` (§ 2.2.1). `publish` gains `carry` and `change`: it applies the rules, composes the body in § 2.4, and posts a `COMMENT`. `OWN_MARKER` also matches `projector-carry`, and `SIGNATURE` accepts the `carried from` segment. |
| `src/projector/cli.py` | `review interdiff <number>`, with `--json`. `review publish` gains `--carry` and `--change`, and requires `--verdict`, `--body`, and `--covered` only without `--carry`. Both read `review.carry_max_lines`. |
| `src/projector/site/__init__.py` | `review_statuses` reads each body with `verdict_marker` and adds `carriedFrom` to a carried head's status. |
| `site/src/globals.d.ts`, `site/src/summary.ts` | The optional `carriedFrom` on a clean status, and the header text **Clean (carried from `<short-sha>`)**. |
| `site/assets/summary.js` | Rebuilt with `npm run build`. |
| `skills/review-pr/SKILL.md` | A section, "Carry a clean verdict across a minimal change", with steps 2 to 4 of § 2.1, the kinds in § 2.2.2, and the publish command. The section on labels describes the carry marker, and the report names the carried-from head. A full re-review may read its new range from `interdiff`'s patch. |
| `skills/start-review-loop/SKILL.md` | "Continue after fixes" says that every pushed SHA ends in a published verdict on that SHA, full or carried. A subagent's report names the carried-from head. |
| `docs/cli.md` | The `review.carry_max_lines` row, `review interdiff`, `publish --carry`, and the header's carried status. |
| `tests/test_review.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **Post a carried verdict, never skip the head.** A head with no verdict
  reads as unreviewed everywhere. `fix-pr` holds a pull request back from
  clean until a clean verdict names its current head, and the page header
  shows **Unreviewed**. A posted verdict keeps every reader working without a
  change to any of them. The body is short because the earlier review already
  says the rest.
- **Bound the shape in code, and judge the content.** Size alone does not make
  a change safe, because one line can invert a condition. Judgment alone has
  no bound, and nothing would stop a reviewer from carrying a restyle of 300
  lines. The code enforces what needs no judgment, so the same rules hold every
  time and tests prove them. The reviewer decides only whether the lines it
  read are presentation, prose, or formatting. The skill lists the kinds that
  carry rather than the kinds that do not, so a kind nobody listed gets a full
  review.
- **Measure the pull request's own change.** `fix-pr` merges the base whenever
  a branch conflicts, and that merge can bring in thousands of lines the pull
  request does not own. `merge-tree` gives the change that Git would show for
  the pull request if both heads sat on the same base. Rejected: `git diff O N`,
  which counts the merge, and `git range-diff`, which compares commit by commit
  and gives no single count for the change. A rebase fails the ancestor rule
  and gets a full review.
- **Measure from the last full review.** If each carry measured from the head
  before it, twenty one-line pushes would carry twenty lines that no full
  review read, each push under the limit. Measuring from the head the newest
  full review covered keeps what any carried verdict leaves unread within
  `review.carry_max_lines`.
- **Record the carry on a line of its own.** `review_statuses` matches the
  review marker with the anchored `MARKER` pattern, which ends at
  `covered=<n>/<n> -->`. A `carried=` field inside the marker would fail that
  match on every site built by an earlier release, and such a site would show
  a carried head as **Unreviewed**. A separate `projector-carry` line leaves
  the marker exactly as every reader parses it, and only a reader that knows
  the carry line reads it.
- **Publish through `review publish`.** Claude Code's auto mode refuses a
  clean self-review's publish as self-approval unless the rule
  `Bash(project review publish *)` allows it, and `project init` adds that rule
  and its Codex counterpart. A new publishing command such as
  `project review carry` would fall outside both rules, and every user's carry
  would be refused until they ran `init` again. As a flag of `publish`, a carry
  also reuses the lock, the head check, the collision check, the read-back, the
  loop's record, and the cleanup, without a copy of any of them.
- **Let publish write the body.** Publish already writes the signature line and
  the marker so that the duration, `seconds=`, and `sha=` are never typed. The
  carried sentence follows the same rule, so the earlier head it names, the
  counts, and the link cannot disagree with the markers. The reviewer supplies
  only the phrase that says what changed.
- **Use one key.** `review.carry_max_lines = 0` turns carrying off, so a
  separate on-off key would only repeat it. The default of 20 admits each run
  of stylesheet pushes on #209, whose largest total since a full review was 15
  lines, and stays small enough for a reviewer to read all of it with its
  context. Carrying is on by default because the loop already decides every
  head, and a carried verdict says that it is one on GitHub and on the site.
- **Carry only a trusted head, and never as an approval.** The trust rule
  decides whether a head's code may run. A carry lets a change skip the method,
  so it needs at least that bar. A trusted head can belong to another author
  with write access, and carrying their padding fix saves the same time.
  Rejected: limiting carries to self-review, which would cost a teammate's pull
  request full runs for no safety the trust rule does not already give. A
  carried verdict posts a `COMMENT` even where `review.allow_approve` is on,
  because an approval vouches for the code, and a carry vouches only that the
  change since a reviewed head was minimal.
- **Run no gate and no tests.** The kinds that carry are ones a test does not
  observe, and CI runs on the push regardless. Running the repository's gate
  would spend the time a carry exists to save.
- **Mark the carried status in the header.** The header is the site's one
  statement of how well a head was checked. A carried verdict rests on a
  judgment about a small change, not on the method, so the header says so, as
  the signature line does on GitHub. The status stays `clean`, with an
  optional `carriedFrom`, so a reader that knows only `clean` keeps working.
  Rejected: plain **Clean**, which hides the difference, and a new status
  value, which every reader of `status` would have to learn.
- **Update the summary rather than skip it or copy it.** Without an update, the
  site would show the earlier head's diff with the old padding, the carried
  verdict would appear nowhere on the site, and the summary links in the
  description and the comment would name the earlier head. Copying the earlier
  summary to the new head is not safe either. A note on a changed line can
  describe code the push replaced, and `summary build` checks only that a
  note's line is still in the diff, as `summarize-pr` says. The existing update
  path is cheap on a small interdiff and needs no new code.

## 4. Acceptance criteria

The tests run in temporary Git repositories against the fake GitHub that
`tests/test_review.py` already uses.

1. **Measurement.** A commit on top of the earlier head counts only its own
   lines. A merge of a trunk that changed 50 lines of another file, plus a
   one-line edit, counts the edit alone. A trunk change to the same lines,
   resolved by the author in the merge, refuses as unmeasurable. A rebase
   refuses because the earlier head is not an ancestor. With `merge-tree`
   failing as an older Git does, a moved merge base refuses, and an unmoved one
   still measures.
2. **Rules.** Each row of the table in § 2.2.1 refuses with its own reason:
   carrying turned off with `0`, an untrusted head, a newest verdict that
   requests changes, a newest verdict on this head, no verdict, an open
   finding, each of an added, deleted, renamed, mode-changed, binary, and
   symlinked file, and a change over the limit. A clean verdict from another
   account is ignored. A `review.carry_max_lines` that is a string, a negative
   number, or `true` refuses and names the key. When the newest verdict was
   carried from head F, the change is measured from F, and it refuses when the
   total since F is over the limit even though the last push alone is not.
3. **Publish.** On a self-review, `publish --carry` posts one `COMMENT` whose
   body has exactly the parts in § 2.4. Its marker line matches `MARKER`, its
   carry line names F, and its sentence links F's review. Publish marks the
   pull request ready, deletes the start comment, records the review in the
   loop's record, and releases the lock. On another author's pull request with
   `review.allow_approve = true`, it posts a `COMMENT` with the approval
   sentence. It refuses and keeps the lock when a rule fails at publish time,
   such as a thread opened since `interdiff` ran, or when the head moved. It
   refuses `--carry` with `--threads`, `--body`, `--covered`,
   `--second-verdict`, or `--verdict changes-requested`, and a `--change` that
   is empty, longer than one line or 200 characters, or holds `<!--`. An
   ordinary publish refuses a body that holds `<!-- projector-carry`.
4. **Readers.** The collision check refuses a publish on a head where another
   loop posted a carried verdict. `review_statuses` gives a carried review's
   head `carriedFrom` and a full review's head none, and ignores a carry line
   in a body that has no review marker. A test under node renders the header
   of a page whose status carries `carriedFrom` as
   **Clean (carried from a8cf3d5)**.
5. **By hand.** In a scratch repository with a review loop running, give a pull
   request a clean full review, then push a one-line padding change. The loop
   posts a carried verdict, the pull request stays ready, and the summary for
   the new head shows the carried status in its header. Then push a one-line
   change to a condition, and the loop runs a full review. The implementing
   pull request's Testing section records these steps.

Criteria 1 to 4 fail before the change, because neither `review interdiff` nor
`publish --carry` exists.

## 5. Cost

- **The check on each new head.** When the reviewer has an earlier clean
  verdict, `interdiff` adds one paginated listing of the pull request's
  reviews, the census, two `git merge-base` calls, at most one
  `git merge-tree`, and one `git diff`. That is a few requests and local Git
  commands, against the clean full reviews on #209, which took 76 to 116
  seconds each.
  Publish shares one listing between the collision check and the carry rules.
- **What a carry saves.** The method's passes, the reading outward from each
  changed definition, the gate and focused tests, and the long body. On #209
  the eight heads in § 1 took 638 seconds of full review. Each ran 514 unit
  tests, the site's type check and build, and both plugin validations.
- **What a carry still spends.** Setup still fetches the head, creates the
  worktree, looks up trust, and posts the start comment. The reviewer reads at
  most `review.carry_max_lines` lines with their context. Publish posts the
  review, and the summary update republishes the page, which deploys the site
  once, as after any review.
- **How it is measured.** The marker's `seconds=` already records each
  verdict's time. The implementing pull request reports the `seconds=` of a
  carried verdict and of a full review on the same scratch pull request.

## 6. Open questions

None blocks implementation. These are deferred on purpose:

- **A list of paths that never carry.** A key such as `review.carry_exclude`
  could name files a repository wants fully reviewed whatever the change.
  The kinds in § 2.2.2 and the rules on file shape cover every case this plan
  found, so the key waits until a repository needs it.
- **Carrying across a rebase.** A rebase fails the ancestor rule and gets a
  full review. Comparing the two heads with `git range-diff` can follow if
  rebased pull requests turn out to pay for many full reviews of small
  changes.

## 7. Background

The two runs of stylesheet pushes on #209. Each run starts after a head whose
push was larger and got a full review. The change is measured from that head
with `git diff --shortstat`, as the carry rules measure it, and the review
time is the `seconds=` field of each head's review marker:

| Head | Run starts after | Change since then | Review time |
| --- | --- | --- | --- |
| `6bc2aeb` | `899a3a7` | +2 −1 | 77 s |
| `e2139e2` | `899a3a7` | +3 −2 | 76 s |
| `2d3a3d5` | `899a3a7` | +5 −2 | 77 s |
| `a73fb26` | `899a3a7` | +8 −5 | 76 s |
| `b57e5be` | `899a3a7` | +9 −6 | 78 s |
| `f02368c` | `7225ba0` | +1 −1 | 86 s |
| `a8cf3d5` | `7225ba0` | +5 −6 | 85 s |
| `ad1e106` | `7225ba0` | +5 −6 | 83 s |

Every change in the table touches only `site/assets/summary.css`. The
measurement assumes that each head in a run would have carried. A head whose
change the reviewer judged to be more than presentation would have had a full
review and started a new run. The second run holds one such change: before
`a8cf3d5`, a single rule replaced the three rules that sized the page's
checkboxes, which changes what the rules select.

Reading the code settled these points:

- `review_statuses` in `src/projector/site/__init__.py` imports `MARKER` from
  `src/projector/review.py` and matches it against each line of a review body.
  The pattern is anchored at both ends, so an added field breaks the match.
- The collision check, `reviewer_verdicts`, matches the looser
  `projector-review .* sha=<head>`, so a marker in an older shape, such as one
  with `verdict=approved` and no `projector=` field, still collides, as
  `tests/test_review.py` checks. The carry rules need the verdict itself, so
  they read the strict `MARKER`, while the collision check keeps the loose
  match over the same listing.
- `RULES` in `src/projector/permissions.py` allows
  `Bash(project review publish *)` and `Bash(project summary publish *)`, and
  nothing else.
- `setup` stores the new head's merge base as `base` in the review's state, so
  the measurement needs no fetch beyond the one `setup` already makes.
