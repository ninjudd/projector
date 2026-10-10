---
status: ready
priority: next
---

# Review a minimal push incrementally

When a push changes a cleanly reviewed pull request only a little, the review
loop reads just that change and publishes an incremental review: a short clean
verdict that names the full review it builds on, in place of another full
review.

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
`project review interdiff <number>`. The command finds this loop's last full
review of the pull request and measures the pull request's own change since
that review's head, whether the author pushed a commit, merged the base
branch, rebased, or rewrote a commit. It leaves out what the base brought in
and shows how the author resolved any conflict. It also checks the rules that
need no judgment: incremental reviews are on, a loop runs the review, the head
is trusted, the earlier full review is this loop's own and was made by the
same Projector version, model, and effort, no finding is open, the change can
be measured, every changed file can be read, and, when the merge base moved,
the repository's gate passed on the new head.

When those rules pass, the subagent reads every line of the change and asks
one question: would the full method, with its gate, focused tests, and reading
outward from each changed definition, catch something this read cannot? When
the answer is no, it writes a short body saying what changed and what it
checked, and runs
`project review publish <number> --incremental --body <file>`. Publish checks
the same rules again and posts a clean review: the signature line, the
markers, a lead that links the earlier full review with the size of the
change, and the subagent's body. Any other outcome is a full review, as today.
An incremental review is a clean verdict on the new head with the existing
review marker, plus one marker line that names the full review's head, so
everything that reads a verdict keeps working.

### 2.1 The flow on a new head

1. The subagent runs `project review setup <number>` as today. Setup fetches
   the head, creates the scratch worktree, records whether the head is trusted,
   takes the lock, and posts the start comment.
2. The subagent runs `project review interdiff <number>`. The command reads the
   review's state and the loop's record, applies the rules in § 2.2.1, and
   exits 0 when they allow an incremental review. It prints the earlier full
   review, how the head moved since it, the counts, any conflicted paths, and
   the interdiff's patch, as § 2.3 shows. It exits 1 when a rule fails, naming
   the rule, and still prints the patch when it got as far as measuring one.
   When the only rule that fails is the gate rule, the output says that the
   gate is what remains, as `needs_gate` under `--json`, and the subagent goes
   on to step 3. Any other non-zero exit means a full review. That includes
   the exit from a CLI too old to know the command, so a skill newer than the
   installed CLI falls back to today's behavior.
3. On exit 0, or when the gate is what remains, the subagent reads the whole
   patch, and the code around each hunk at the head when it needs context, and
   decides as § 2.2.2 says. When the read decides for an incremental review
   and the gate is what remains, the subagent runs
   `project review gate <number>` and goes on only when the gate exits 0.
   Otherwise it continues at step 5 of `review-pr`, the full method, on the
   setup from step 1. A problem the read finds goes to that full review, which
   files it.
4. The subagent writes a short body, as § 2.4 says, and runs
   `project review publish <number> --incremental --body <file>`. Publish
   checks the lock and the head as it does today, applies the rules again to
   the live state, runs the collision check, composes the review in § 2.4, and
   posts it as a `COMMENT`. It then sets draft state, re-reads the review,
   records it in the loop's record, deletes the start comment, and releases
   the lock. A refused publish keeps the lock, so the subagent can run the
   full review on the same setup. When the head moved, the subagent runs
   `project review move <number>` and starts again at step 2.
5. The subagent updates the summary for the new head, as § 2.7 says. It
   reports the SHA, the clean verdict, the review id, no threads, and the head
   of the full review the incremental review builds on.

### 2.2 When a review is incremental

#### 2.2.1 The rules the command checks

`project review interdiff` and `publish --incremental` share one function for
these rules. It refuses at the first rule that fails:

| Rule | Refused when |
| --- | --- |
| Incremental reviews are on | `review.incremental` is `false`. |
| A loop runs the review | The review's state names no loop, as when a person runs `review-pr` by hand. |
| The head is trusted | `setup` recorded the head as untrusted, by the rule `review-pr` uses to decide whether a head's code runs. |
| This loop's clean full review precedes it | The loop's record holds no full review of the pull request, or the newest verdict in it, full or incremental, requests changes or names this same head. |
| The same reviewer made it | The earlier full review is missing from GitHub, or its marker names a different `projector` version, `model`, or `effort` than this run would write. |
| No finding is open | The census counts an open Projector finding thread. |
| The change can be measured | The checkout lacks the earlier head or its recorded merge base, or the replay in § 2.3 fails, as it does on a Git older than 2.40 when the merge base moved. |
| Every file can be read | The interdiff touches a binary file, a symlink, or a submodule. |
| The base's changes passed the gate | The merge base moved since the earlier head (`MO` differs from `MN` in § 2.3), and the review's state records no `project review gate` run on this head that exited 0. |

The earlier full review is the newest full review in this loop's record for
the pull request, and its head is the earlier head `O`. When the newest
verdict is itself incremental, it builds on that same full review, so the
change is always measured from the last full review, and every incremental
review names a fully reviewed head.

The same-reviewer rule compares the earlier review's marker with what this run
would write: the installed Projector version, the model in the review's state,
and the effort this run reads. A marker with no `effort=` matches only a run
that reads no effort.

The gate rule covers what the interdiff leaves out. A merge or a rebase onto a
moved base can break the pull request without changing a line of the
interdiff. When the base changes the signature of a function that the pull
request calls from a file of its own, the replay is clean, the interdiff can
be empty, and only running the code shows the break. The gate is the check a
full review of that head would run. `project review gate` already records its
exit status and head in the review's state, and the rule reads that record,
so the reviewer's word is not what says the gate passed. Where
`.projector.toml` sets no `review.gate`, `gate` refuses and records nothing,
so a head whose base moved gets a full review, which runs the checks the
repository's instructions name. The command checks the gate rule last, so a
refusal that names it means every other rule passed.

An added, deleted, renamed, or mode-changed file is not refused. The patch
shows each one, and the reviewer judges it like any other hunk.

#### 2.2.2 What the reviewer decides

The command settles the facts that a read of the patch cannot see. The
reviewer reads every line of the interdiff, with the code around it at the
head where it needs context, and asks one question: would the full method,
with its gate, focused tests, and reading outward from each changed
definition, catch something this read cannot? The review is incremental only
when the answer is no. Run the full review when the answer is yes, when a hunk
reworks code an earlier finding thread was about, or when you are unsure. A
needless full review costs a minute or two. A wrong incremental review signs
off a change that no pass read.

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
`guidelines.md` § 1 as well. A comment that misstates the code is a
correctness finding there, and one that narrates the code beneath it, excuses
complexity, or asserts that a decision is correct is a written-rules finding.

How the head moved, the `kind` in § 2.3, also guides the decision:

- **A pushed commit, a clean merge, or a clean rebase** is the usual
  incremental case. The interdiff holds only the pull request's own change.
- **A rewrite in place**, such as an amended or squashed commit, reads like a
  pushed commit. `unchanged_commits` says whether any reviewed commit's
  content changed or only its message did.
- **A resolved conflict** usually needs a full review, because the resolution
  combines two changes that no review read together. Review it incrementally
  only when every resolved hunk is trivial, such as two edits to one
  stylesheet value, both sides adding independent lines, or a version string.

### 2.3 Measuring the pull request's own change

The interdiff is what the pull request's diff against its base gained or lost
between the earlier head `O` and the new head `N`, however `N` was made. A
plain `git diff O N` also counts everything a merge or a rebase brought in
from the base branch.

1. `MO` is the merge base `O` had when its full review ran. `setup` stored it
   as `base` in that review's state, and `publish` copies it into the loop's
   record.
2. `MN` is the new head's merge base, which `setup` records as `base` in this
   review's state.
3. When `MO` equals `MN`, the base did not move, and the comparison tree `T`
   is `O`'s.
4. Otherwise `T` is the tree that
   `git -c merge.conflictStyle=diff3 merge-tree --write-tree --name-only --merge-base=MO MN O`
   writes: `O`'s change replayed onto the new merge base. `merge-tree` needs
   no ancestry between `O` and `N`, so the replay works the same after a
   merge, a rebase, or a force-push. Exit status 0 is a clean replay. Exit
   status 1 means the replay conflicts. The tree still holds every file, with
   conflict markers in each conflicted one, and the output lists their paths,
   which become `conflicts`. Any other status fails the measurement, and so
   does a Git before 2.40, which does not know `--merge-base`.
5. The interdiff is `git diff --no-renames T N`. Its `--numstat` gives the
   insertions, deletions, and files, and its `--raw` gives each file's status
   and modes for the readable rule.

For a conflicted file, the interdiff shows the author's resolution, as
`git show --remerge-diff` shows a merge's. The conflict region leaves, with
the new base's side, the original lines, and the reviewed head's side between
its markers, and the resolved lines arrive in its place. The rest of the file
shows the pull request's own change since `O`. The command sets the diff3
style so that the original lines show whatever the operator's configuration
says. Each marker names its commit by full SHA: `MN` after `<<<<<<<`, `MO`
after `|||||||`, and `O` after `>>>>>>>`. The counts include the markers and
both sides, so a one-line resolution counts about +1 −7. A conflict that is
not textual, such as one side deleting a file the other edited, shows as the
whole file kept or removed. What the interdiff cannot show is how the new
base's other changes meet the pull request's code, and the gate rule covers
that, because a conflict means the base moved.

The recorded merge base matters after a stack's parent is rewritten. When a
fix amends the parent's commit and the child is rebased onto it,
`git merge-base O MN` gives the commit below the parent's old commits. The
replay then brings those old commits along, they conflict with their amended
versions, and the interdiff shows a conflict in the parent's file that the
child's author never resolved. The recorded merge base is the parent's old
tip, so the replay carries only the child's own change.

The command labels how `N` relates to `O` with a `kind`. The measurement is
the same for every kind, and no rule reads it:

| Kind | When |
| --- | --- |
| `push` | `O` is an ancestor of `N`, and the merge base did not move. |
| `merge` | `O` is an ancestor of `N`, and the merge base moved, which takes a merge of the base branch. |
| `rebase` | `O` is not an ancestor of `N`, and the merge base moved. |
| `rewrite` | `O` is not an ancestor of `N`, and the merge base did not move, as after an amend, a squash, or a reword in place. |

For `rebase` and `rewrite`, the command also compares the
`git patch-id --stable` of each commit in `MO..O` with those in `MN..N`,
leaving out merges, as `git range-diff` matches commits. When every reviewed
commit has a new commit with the same patch id, every reviewed commit carried
over unchanged, and the interdiff holds only the commits added after them.

`merge-tree` writes objects to the checkout's object store and touches neither
the working tree nor the index, so the operator's uncommitted work stays as it
is.

For example, in a scratch repository the reviewed head `O` changed `.r1`'s
padding from 8px to 9px, the base branch then changed it to 12px, and the
author rebased onto the base and resolved the conflict to 10px. Before the
gate has run, `project review interdiff` prints:

```
earlier full review: 7b4ab3a, https://github.com/<owner>/<repo>/pull/<number>#pullrequestreview-<id>
kind: rebase onto main; a reviewed commit changed
conflicts: a.css
change: +1 −7 lines in 1 file
refused: the merge base moved, and no `project review gate` run on this head has exited 0 (needs_gate)

diff --git a/a.css b/a.css
index 20add67..fb6f4d4 100644
--- a/a.css
+++ b/a.css
@@ -1,11 +1,5 @@
 .r0 { padding: 8px; }
-<<<<<<< 44f3f85b918d7b1840e96fefad4548a211233c59
-.r1 { padding: 12px; }
-||||||| f18cd72c5aa41bbd69923b977c668478cbc71cb5
-.r1 { padding: 8px; }
-=======
-.r1 { padding: 9px; }
->>>>>>> 7b4ab3a496fce36044639ed62a00095601e9e57a
+.r1 { padding: 10px; }
 .r2 { padding: 8px; }
 .r3 { padding: 8px; }
 .r4 { padding: 8px; }
```

The reviewer sees the base's 12px, the original 8px, the reviewed 9px, and
the author's 10px. Both sides edited one stylesheet value, which is the kind of
trivial resolution § 2.2.2 allows, so the reviewer may run the gate and, when
it passes, publish an incremental review.

With `--json`, `interdiff` prints one object, with the same exit status:

| Field | Value |
| --- | --- |
| `incremental` | `true` when every rule in § 2.2.1 passes. |
| `reason` | The rule that failed, in words, or `null`. |
| `needs_gate` | `true` when the gate rule is the only one that fails, and `false` otherwise. |
| `from` | The full SHA of the earlier head, or `null` when there is none. |
| `review` | The URL of that head's full review, or `null`. |
| `kind` | `push`, `merge`, `rebase`, or `rewrite`, or `null` when the change was not measured. |
| `unchanged_commits` | For `rebase` and `rewrite`, `true` when every reviewed commit carried over unchanged and `false` otherwise; `null` for the other kinds. |
| `conflicts` | The paths the replay conflicted in, `[]` when it was clean or not needed, or `null` when the change was not measured. |
| `insertions`, `deletions` | The interdiff's line counts, or `null` when it was not measured. |
| `files` | The paths the interdiff touches, empty when it was not measured. |
| `patch` | The interdiff, or `""` when it was not measured. |

The exit status and `conflicts` keep a resolved conflict apart from a change
that cannot be measured. A measured change with conflicts lists their paths
and exits 0 when the rules pass. A change that cannot be measured exits 1, and
its `conflicts` is `null`.

### 2.4 The incremental review

Publish writes the signature line, both markers, and a lead, so the earlier
head, its link, the kind, and the counts always agree with the markers. The
subagent writes the body that follows: a few sentences on what changed and
what it checked about it, at most 600 characters. For the push in § 1 the
review reads:

```
📽️ **Projector review** · `0.7.2` · model `claude-opus-5-5` · effort `high` · **CLEAN** · incremental from `a8cf3d5` · took 0m 38s

<!-- projector-review v=1 verdict=clean projector=0.7.2 model=claude-opus-5-5 effort=high sha=ad1e1065bd69f6d819b16cc6ebe3330d63042b52 findings=0 seconds=38 covered=1/1 -->
<!-- projector-incremental v=1 from=a8cf3d521b8ec3d6b430e4b59fa2b7415473e92e kind=push insertions=2 deletions=2 files=1 conflicts=0 -->

Incremental review of the change since the full review of [`a8cf3d5`](https://github.com/ninjudd/projector/pull/209#pullrequestreview-5479885726): +2 −2 lines in 1 file.

The push widens the side padding of the sidebar's pull request rows and project rows from 8px to 9px in `site/assets/summary.css`. Both edits change only a `padding` value, so each selector still matches the same elements, and nothing is shown, hidden, or made clickable that was not before. The gate, the tests, and reading outward would find nothing these two lines do not show.
```

The review contains, in this order:

1. **The signature line**, with `incremental from <short-sha>` between the
   verdict word and the duration. The duration runs from the start comment,
   as today.
2. **The review marker**, in exactly today's grammar. `verdict=clean`,
   `findings=0`, and `covered=<n>/<n>`, where `n` is the number of files the
   interdiff touches, the files this verdict read.
3. **The incremental marker**, on the line after the review marker, with the
   fields in the table below.
4. **The lead**: "Incremental review of the change since the full review of
   `<short-sha>`: +`<insertions>` −`<deletions>` lines in `<files>` file or
   files." The short SHA links to the full review. For a kind other than
   `push`, a phrase before the colon names it: "across a merge of `<base>`",
   "across a rebase onto `<base>`", or "across a rewrite of its commits".
   After a conflict, the lead ends "with a resolved conflict in `<path>`",
   naming each conflicted path.
5. **The subagent's body.** On another author's pull request, publish adds
   one sentence after it: "A human approval is what remains."

The review leaves out what the earlier full review already says and what an
incremental review does not do: the intent paragraph, the census, the
coverage line, disclosures, `Suggestions`, and the list of checks. The census
still decides the verdict, because publish refuses an incremental review while
any finding is open, but it does not print.

| Incremental marker field | Value |
| --- | --- |
| `v` | `1` |
| `from` | The full SHA of the head of the full review this one builds on, the head the lead links. |
| `kind` | The `kind` from § 2.3. |
| `insertions`, `deletions` | The interdiff's line counts. |
| `files` | The number of files the interdiff touches. |
| `conflicts` | The number of files the replay conflicted in. |

`--incremental` needs `--body`, and refuses `--threads`, `--covered`,
`--second-verdict`, and `--verdict changes-requested`. Its verdict is always
clean. The body must hold at most 600 characters once its placeholders are
filled, with no signature line or marker, as a full review's body may not, and
no `{census}`, since an incremental review prints none. Without
`--incremental`, `--verdict`, `--body`, and `--covered` stay required. An
incremental review is always a `COMMENT`, even where `review.allow_approve` is
`true`. On a self-review, publish then marks the pull request ready, as for
any clean verdict. An ordinary review body that holds a
`<!-- projector-incremental` line is refused, as one that holds its own review
marker is today.

Each entry publish adds to the loop's record gains two fields: `base`, the
merge base from the review's state, which a later `interdiff` reads as `MO`,
and `from`, the incremental marker's `from=`, or `null` for a full review.

### 2.5 Configuration

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `review.incremental` | boolean | `true` | `project review interdiff` and `project review publish --incremental`, to allow an incremental review of a minimal push. `false` gives every head a full review. |

Both commands read the key from the review's checkout, as `publish` reads
`review.allow_approve`. A value that is not `true` or `false`, such as a string
or a number, makes both commands refuse and name the key.

### 2.6 What reads an incremental review

| Reader | What it reads | With an incremental review |
| --- | --- | --- |
| Draft state | `publish` marks a self-reviewed pull request ready on a clean verdict. | Marks it ready by the same path. |
| Collision check | The reviewer's reviews whose body matches `projector-review .* sha=<head>`. | Matches, because the marker's `sha=` is the new head. An incremental review collides and is recorded like any verdict, and `setup --rereview` still allows a re-review. |
| Publish's read-back | `sha=<head>` in the posted body. | Unchanged. |
| Leftover start comments | A Projector review on the comment's `sha=` submitted after it. | Unchanged. |
| Page header | `review_statuses` matches each body line against the anchored `MARKER` pattern. | Matches the unchanged marker, also reads the incremental line, and adds `incrementalFrom`: the full review's head and URL. The header shows **Clean · incremental from a8cf3d5**. **Clean** links to the incremental review, and **incremental from a8cf3d5** links to the full review. A site built by an earlier release matches the same marker line and shows plain **Clean**. |
| Fix loop watcher | A `REVIEW` event for each `COMMENTED` review with a body. | Announces the incremental review once, like any clean review. `fix-pr` reads `CLEAN` and finds nothing outstanding. |
| `fix-pr`'s clean test | "A clean verdict names its current head." | Holds. |
| `fix-pr`'s reviewed-head test | "The current head is the one actually reviewed." | Holds. The incremental review names the current head, and its reviewer read every line the head changed since a fully reviewed head. `fix-pr` needs no change. |
| Review loop watcher | Heads and threads only. | Unchanged. |

### 2.7 The summary

After an incremental review, the subagent updates the summary for the new head
as it does after any review, through the update path in `summarize-pr`,
"Update the page when the pull request moves." On a small interdiff the update
is mostly a new `pr.head` and a note moved or rewritten on a changed line. The
site then shows the new head's diff, and the header of its page shows the
incremental review.

### 2.8 Files that change

| File | Change |
| --- | --- |
| `src/projector/review.py` | `INCREMENTAL`, the incremental marker's pattern beside `MARKER`, and `verdict_marker(body)`, which returns a body's verdict, head, and incremental-from head for both the rules and the site. `reviewer_reviews`, one paginated listing of the reviewer's Projector reviews with id, URL, time, and body: `check_collision` filters it by `sha=` as today, and the rules read the earlier full review's marker from it. `interdiff` (§ 2.3) and `incremental_rules` (§ 2.2.1), which read the loop's record and the gate record that `gate` already writes to the review's state. `publish` gains `incremental`: it applies the rules, composes the review in § 2.4, posts a `COMMENT`, and adds `base` and `from` to the loop's record. `OWN_MARKER` also matches `projector-incremental`, and `SIGNATURE` accepts the `incremental from` segment. |
| `src/projector/cli.py` | `review interdiff <number>`, with `--json`. `review publish` gains `--incremental`, and requires `--verdict` and `--covered` only without it. Both read `review.incremental`. |
| `src/projector/site/__init__.py` | `review_statuses` reads each body with `verdict_marker` and adds `incrementalFrom` to an incremental review's head. |
| `site/src/globals.d.ts`, `site/src/summary.ts` | The optional `incrementalFrom` on a clean status, and the header segment **incremental from `<short-sha>`**, linked to the full review. |
| `site/assets/summary.js` | Rebuilt with `npm run build`. |
| `skills/review-pr/SKILL.md` | A section, "Review a minimal push incrementally", with steps 2 to 4 of § 2.1, the gate step for a moved base, the deciding question and the guidance in § 2.2.2, what the body says, and the publish command. The section on labels describes the incremental marker, and the report names the full review's head. A full re-review may read its new range from `interdiff`'s patch. |
| `skills/start-review-loop/SKILL.md` | "Continue after fixes" says that every pushed SHA ends in a published verdict on that SHA, full or incremental. A subagent's report names the full review an incremental review builds on. |
| `docs/cli.md` | The `review.incremental` row, `review interdiff`, `publish --incremental`, and the header's incremental status. |
| `tests/test_review.py`, `tests/test_site.py` | The tests in § 4. |

## 3. Why this design

- **Post an incremental review, never skip the head.** A head with no verdict
  reads as unreviewed everywhere. `fix-pr` holds a pull request back from
  clean until a clean verdict names its current head, and the page header
  shows **Unreviewed**. A posted verdict keeps every reader working without a
  change to any of them. The body is short because the earlier review already
  says the rest.
- **Let judgment decide, and enforce only the facts it cannot see.** Size does
  not make a change safe. One line can invert a condition, and forty lines of
  rewritten comments can be harmless. A line limit would refuse the second
  and admit the first, so the reviewer decides by reading every line and
  asking whether the full method would catch more. The code enforces what a
  read of the patch cannot know: who made the earlier review, whether a
  finding is open, whether the head's code may run, whether the change could
  be measured and read, and whether the gate passed after the base moved.
  Those rules hold every time, and tests prove them. The lead prints the
  size, so a large incremental review is plain to every reader. The kinds in
  § 2.2.2 guide the decision rather than bound it, and their exclusions name
  the changes that look like prose but change behavior.
- **Build only on this loop's own full review.** An incremental review vouches
  for a push because the full review before it read the rest, and that holds
  only when the same reviewer made the full review. So the earlier review must
  be in this loop's record and must name the same Projector version, model,
  and effort. A clean verdict from another loop on the same account, or one
  made before an upgrade, leads to a full review. Without a loop, as when a
  person runs `review-pr` by hand, a review is always full. The rule says
  nothing about who wrote the pull request, so a teammate's pull request gets
  incremental reviews when this loop made its full review. When more than one
  model reviews a pull request, the same rule keeps each model's incremental
  reviews to its own full reviews.
- **Measure the pull request's own change, however the head moved.** A head
  moves by a pushed commit, a merge of the base, a rebase, or a rewrite in
  place. Clean merges come from GitHub's **Update branch** button and from a
  stack sync after a parent merges. Rebases come from a cascade after a
  parent's fix and from rebasing onto a moved trunk. `fix-pr` merges the base
  when the branch conflicts with it, so its merges usually bring a resolved
  conflict. Replaying `O`'s change onto the new merge base with `merge-tree`
  gives, for each of these, the change Git would show for the pull request if
  both heads sat on the same base. The replay needs no ancestry, so a rebase
  and a force-push measure like a merge. `MO` comes from the loop's record
  rather than from `git merge-base O MN`, which is wrong after a stack's
  parent is rewritten, as § 2.3 shows. Rejected: `git diff O N`, which counts
  the base's changes, and `git range-diff`, which compares commit by commit
  and gives no single change to read. The patch ids `range-diff` matches by
  still label a rebase that carried every commit over unchanged.
- **Show a resolved conflict, and let the reviewer judge it.** A resolution is
  where two changes meet that no review read together, so it usually needs a
  full review. Some resolutions are trivial, such as two edits to one
  stylesheet value, and refusing every conflict would give those a full
  review and its long body too. The interdiff shows the resolution against
  both sides and the original, as `git show --remerge-diff` does, so the
  reviewer can tell which case it is. The gate rule runs the code after every
  conflict, because a conflict means the base moved.
- **Measure from the last full review.** If each incremental review measured
  from the head before it, twenty one-line pushes would each read one line,
  and no review would read the twenty together. Measuring from the last full
  review means each incremental review reads everything no full review has
  read, and its lead shows how much that has grown.
- **Record the incremental review on a line of its own.** `review_statuses`
  matches the review marker with the anchored `MARKER` pattern, which ends at
  `covered=<n>/<n> -->`. An `incremental=` field inside the marker would fail
  that match on every site built by an earlier release, and such a site would
  show the head as **Unreviewed**. A separate `projector-incremental` line
  leaves the marker exactly as every reader parses it, and only a reader that
  knows the incremental line reads it.
- **Publish through `review publish`.** Claude Code's auto mode refuses a
  clean self-review's publish as self-approval unless the rule
  `Bash(project review publish *)` allows it, and `project init` adds that rule
  and its Codex counterpart. A new publishing command such as
  `project review incremental` would fall outside both rules, and every
  user's incremental review would be refused until they ran `init` again. As a
  flag of `publish`, an incremental review also reuses the lock, the head
  check, the collision check, the read-back, the loop's record, and the
  cleanup, without a copy of any of them.
- **Let publish write what must agree, and the reviewer write the rest.**
  Publish already writes the signature line and the marker so that the
  duration, `seconds=`, and `sha=` are never typed. It writes the incremental
  marker and the lead the same way, so the earlier head, its link, the kind,
  and the counts cannot disagree. The reviewer writes only what a reader needs
  about this push: what changed and what it checked. The 600-character limit
  keeps that to a few sentences, since a push that needs more is a full
  review's.
- **Use one opt-out key.** Incremental reviews are on by default because the
  loop already decides every head, and an incremental review says that it is
  one on GitHub and on the site. `review.incremental = false` serves a
  repository that wants every head through the full method.
- **Review incrementally only a trusted head, and never as an approval.** The
  trust rule decides whether a head's code may run. A push from outside the
  repository is where a line that looks like presentation is likeliest to
  hide more, and its code cannot run for the gate, so it always gets the full
  method. An incremental review posts a `COMMENT` even where
  `review.allow_approve` is on, because an approval vouches for the code, and
  an incremental review vouches only that the change since a reviewed head
  needed nothing more.
- **Run the gate only when the base moved.** While the merge base stays where
  it was, the head differs from code a full review read and gated only by the
  interdiff, and the reviewer has judged that the gate would find nothing in
  it. Running the gate there would spend the time an incremental review
  exists to save. A merge or a rebase onto a moved base breaks that premise.
  It brings in code that no review of this pull request ran beside the pull
  request's own, and a clean replay can still break a call. The gate is the
  check in a full review that catches such a break, so an incremental review
  across a moved base needs a passing gate on the head, and still skips the
  method's passes and the long body. The rule compares with the last full
  review's merge base, so every incremental review after the base moves runs
  the gate until the next full review. Rejected: relying on CI, because a
  verdict does not wait for CI and many repositories have none, and a full
  review for every moved base, which would give one and its long body to
  each small push after the base is merged or rebased onto.
- **Keep incremental reviews clean.** A problem the read finds goes to a full
  review, which verifies it and files it as a thread. An incremental verdict
  that requests changes would need its own rules for threads, the census, and
  draft state, and § 6 defers it.
- **Mark the incremental status in the header.** The header is the site's one
  statement of how well a head was checked. An incremental review rests on a
  read of a small change, not on the method, so the header says so, as the
  signature line does on GitHub, and links the full review it builds on. The
  status stays `clean`, with an optional `incrementalFrom`, so a reader that
  knows only `clean` keeps working. Rejected: plain **Clean**, which hides the
  difference, and a new status value, which every reader of `status` would
  have to learn.
- **Update the summary rather than skip it or copy it.** Without an update, the
  site would show the earlier head's diff with the old padding, the
  incremental review would appear nowhere on the site, and the summary links
  in the description and the comment would name the earlier head. Copying the
  earlier summary to the new head is not safe either. A note on a changed
  line can describe code the push replaced, and `summary build` checks only
  that a note's line is still in the diff, as `summarize-pr` says. The
  existing update path is cheap on a small interdiff and needs no new code.

## 4. Acceptance criteria

The tests run in temporary Git repositories against the fake GitHub that
`tests/test_review.py` already uses.

1. **Measurement.** With a recorded full review at `O`, each case gives this
   interdiff and kind:
   - A commit on top of `O` counts only its own lines, as `push`.
   - A merge of a trunk that changed 50 lines of another file, plus a
     one-line edit, counts the edit alone, as `merge`.
   - A clean rebase onto a moved trunk, plus a new commit, gives exactly that
     commit's change, as `rebase` with `unchanged_commits` true.
   - A rebase with a resolved conflict, and a merge with one, list the file in
     `conflicts`, and the patch shows the diff3 conflict region removed and
     the resolution added. Each exits 0 when the other rules pass.
   - An amend in place gives `rewrite` with `unchanged_commits` false, and a
     reworded message alone gives `rewrite` with true and an empty interdiff.
   - A stack cascade, where the parent's commit is amended and the child is
     rebased onto it with one more edit, counts the edit alone, with no
     conflict.
   - An `O` missing from the checkout refuses as unmeasurable, with
     `conflicts` at `null`. With `merge-tree` failing as an older Git does, a
     moved merge base refuses, and an unmoved one still measures.
2. **Rules.** Each row of the table in § 2.2.1 refuses with its own reason:
   `review.incremental = false`, a review with no loop, an untrusted head, a
   newest verdict in the loop's record that requests changes, one that names
   this head, a record with no full review, a full review whose marker names
   another version, model, or effort, an open finding, and each of a binary
   file, a symlink, and a submodule. A clean full review from another loop on
   the same account leads to a full review. A `review.incremental` that is a
   string or a number refuses and names the key. An added, deleted, renamed,
   or mode-changed file does not refuse. When the newest verdict is
   incremental from F, the change is measured from F. A moved merge base
   refuses with `needs_gate` until a `project review gate` run that exited 0
   on this head is recorded, and then passes. A run that exited non-zero, and
   a passing run recorded on an earlier head, still refuse. An unmoved merge
   base needs no gate run, and an empty interdiff across a moved base still
   needs one.
3. **Publish.** On a self-review, `publish --incremental` posts one `COMMENT`
   whose body has exactly the parts in § 2.4. Its marker line matches
   `MARKER`, its incremental line names F, the kind, and the counts, and its
   lead links F's review. Publish marks the pull request ready, deletes the
   start comment, adds the review to the loop's record with `base` and
   `from`, and releases the lock. After a rebase with a conflict, the lead
   names the rebase and the conflicted file. On another author's pull request
   with `review.allow_approve = true`, it posts a `COMMENT` that ends with the
   approval sentence. It refuses and keeps the lock when a rule fails at
   publish time, such as a thread opened since `interdiff` ran or a moved base
   with no passing gate on the head, or when the head moved. It refuses
   `--incremental` with `--threads`, `--covered`, `--second-verdict`, or
   `--verdict changes-requested`, and a body that is empty, longer than 600
   characters, or holds a signature line, a marker, or `{census}`. An
   ordinary publish refuses a body that holds `<!-- projector-incremental`.
4. **Readers.** The collision check refuses a publish on a head where another
   loop posted an incremental review. `review_statuses` gives an incremental
   review's head `incrementalFrom` with F and F's review URL, gives a full
   review's head none, and ignores an incremental line in a body that has no
   review marker. A test under node renders the header of a page whose status
   carries `incrementalFrom` as **Clean · incremental from a8cf3d5**, with the
   second part linking F's review.
5. **By hand.** In a scratch repository with a review loop running, give a pull
   request a clean full review, then push a one-line padding change. The loop
   posts an incremental review, the pull request stays ready, and the summary
   for the new head shows it in its header. Then push a one-line change to a
   condition, and the loop runs a full review. Then push a one-line comment
   that misstates the code beside it, and the loop runs a full review that
   flags it. Then rebase the branch onto a moved trunk with no other change,
   and the loop runs the gate and posts an incremental review whose lead names
   the rebase. The implementing pull request's Testing section records these
   steps.

Criteria 1 to 4 fail before the change, because neither `review interdiff` nor
`publish --incremental` exists.

## 5. Cost

- **The check on each new head.** When the loop's record holds a full review
  of the pull request, `interdiff` adds one paginated listing of the pull
  request's reviews, the census, one `git merge-base --is-ancestor`, at most
  one `git merge-tree`, and one `git diff`. After a rebase or a rewrite it
  also runs `git log -p` over two ranges through `git patch-id`. That is a few
  requests and local Git commands, against the clean full reviews on #209,
  which took 76 to 116 seconds each. Publish shares one listing between the
  collision check and the rules.
- **What an incremental review saves.** The method's passes, the reading
  outward from each changed definition, focused tests, the long body, and the
  gate unless the base moved. On #209 the eight heads in § 1 took 638 seconds
  of full review. Each ran 514 unit tests, the site's type check and build,
  and both plugin validations.
- **What an incremental review still spends.** Setup still fetches the head,
  creates the worktree, looks up trust, and posts the start comment. The
  reviewer reads the whole interdiff with its context and writes a few
  sentences. When the merge base moved since the last full review, the gate
  runs once on the head. Publish posts the review, and the summary update
  republishes the page, which deploys the site once, as after any review.
- **How it is measured.** The marker's `seconds=` already records each
  verdict's time. The implementing pull request reports the `seconds=` of an
  incremental review and of a full review on the same scratch pull request.

## 6. Open questions

None blocks implementation. These are deferred on purpose:

- **A list of paths that always get a full review.** A key such as
  `review.full_review_paths` could name files a repository wants read by the
  full method whatever the change. The guidance in § 2.2.2 covers every case
  this plan found, so the key waits until a repository needs it.
- **An incremental review that requests changes.** Today the read sends any
  problem it finds to a full review, which files it. If those full reviews
  turn out mostly to file one small finding the read already saw, a later
  change could let the incremental review post it, with rules for its thread,
  the census, and draft state.

## 7. Background

The two runs of stylesheet pushes on #209. Each run starts after a head whose
push was larger and got a full review. The change is measured from that head
with `git diff --shortstat`, as `interdiff` measures a pushed commit, and the
review time is the `seconds=` field of each head's review marker:

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
measurement assumes that each head in a run would have had an incremental
review. A head where the reviewer judged that the full method could catch more
would have had a full review and started a new run. The second run holds one
such change: before `a8cf3d5`, a single rule replaced the three rules that
sized the page's checkboxes, which changes what the rules select.

Reading the code settled these points:

- `review_statuses` in `src/projector/site/__init__.py` imports `MARKER` from
  `src/projector/review.py` and matches it against each line of a review body.
  The pattern is anchored at both ends, so an added field breaks the match.
- The collision check, `reviewer_verdicts`, matches the looser
  `projector-review .* sha=<head>`, so a marker in an older shape, such as one
  with `verdict=approved` and no `projector=` field, still collides, as
  `tests/test_review.py` checks. The rules need the verdict itself, so they
  read the strict `MARKER`, while the collision check keeps the loose match
  over the same listing.
- `RULES` in `src/projector/permissions.py` allows
  `Bash(project review publish *)` and `Bash(project summary publish *)`, and
  nothing else.
- `setup` stores the new head's merge base as `base` in the review's state, so
  the measurement needs no fetch beyond the one `setup` already makes.
- `publish` appends the repository, number, SHA, review id, verdict, and time
  to `loops/<id>/published.json`. The same-version rule means every earlier
  full review the rules accept was published by a release that also records
  `base` and `from` there.

Running `merge-tree` in scratch repositories settled these:

- `--merge-base=MO MN O` replays `O`'s change after a rebase as after a merge,
  and the interdiff is the new commit's change alone.
- A conflicting replay exits 1 and still writes a tree, with markers in each
  conflicted file and the paths listed after the tree's id. With
  `-c merge.conflictStyle=diff3`, the markers include the original lines.
- After a stack cascade, `git merge-base O MN` puts a conflict in the parent's
  file that the child never had, and the recorded merge base gives the
  child's edit alone.
