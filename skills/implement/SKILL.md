---
name: implement
description: Implement a Projector project while keeping its plan and status current, or build a change the user describes directly when it needs no design. Use when the user asks to start, continue, or complete work recorded under docs/projects, or describes a change to build.
---

# Implement

Build from a project's plan, or from the user's description of work small
enough to need no design. Either way, treat the repository and current
runtime behavior as authoritative evidence.

## Start from a description

When the user describes the work rather than naming a project:

1. Read the repository instructions. When the repository has a
   `docs/projects/` directory, run `project list --json`, and when a project
   already covers the work, start from its plan instead, as the next section
   describes. A repository without that directory has no projects.
2. Decide whether the work needs design first. It does when it spans several
   parts of the system, changes an interface that other code depends on, or
   leaves a product choice open. Then stop, tell the user why, and suggest
   `design`. Build it without a design only when the user asks you to.
3. Otherwise, restate the change in a sentence or two, and build it. Ask the
   user only about a choice that the repository cannot answer.

Work built from a description has no plan and no project, so skip the steps
below that need one: the status and priority changes, the plan updates, and
`finish`. Everything else applies: the self-review, the validation gate, and
handing the work over.

## Start from the project

1. Read the repository instructions and run `project show <name> --json`.
2. Read the entry point, its supplemental files, relevant nested projects, and
   the code and documentation they cite.
3. Check every open question before claiming readiness. Resolve a product
   choice with the user when the repository cannot answer it.
4. Run `project status <name> in-progress` only when implementation truly
   begins, and `project priority <name> now` when the work also becomes the
   current focus. Keep both changes in the implementation pull request; do not
   open a status-only change.

## Implement coherent work

Build the requested outcome, not merely the easiest plan item. Keep changes
reviewable and verify each behavior in proportion to its risk. Follow the
repository's branch, stack, commit, review, and merge rules, and
[Hand the work over](#hand-the-work-over) where they are silent.

Apply the review method's passes to your own change before the review loop
does; `../review-changes/method.md` § 3 describes them, and three apply to
nearly every change:

- **Correctness.** Trace each change past the happy path: what happens when
  an input is missing, when the call repeats, when the operation fails or is
  cancelled, and at zero, empty, and maximum. Write the test that pins the
  answer while the reasoning is fresh.
- **Performance**, the Cost pass. Know how often the code you touch runs
  before you add work to it. Measure the paths the plan named as hot, and
  put no new read, allocation, or request inside a loop or a per-request
  path the plan did not budget for.
- **Reuse**, the Simplicity pass. Search the repository before writing a
  helper, and use the one you find. When you write the same shape a second
  time, extract it then, not later. When the change replaces something,
  delete the old path, move every caller, and search for the old name
  afterwards so nothing still refers to what is gone. Leave no stub, no
  commented-out block, and no compatibility wrapper the plan did not ask
  for.

Write to `../guidelines.md`, the code guidelines every Projector skill shares.
The review loop reads your head against them and the fix loop fixes to them,
so a rule skipped here comes back as a finding citing the section you skipped.
Its first rule is about comments: write code that needs none, simplify a
stretch before you explain it, and add a comment only to preserve a
constraint or reason the code cannot carry on its own.

When the work has a plan, update it in the same change whenever
implementation settles a decision, changes scope, reveals a new constraint,
or completes an acceptance criterion. Write the update to `../writing.md`,
as `design` writes the plan. Append numbered sections rather than
renumbering cited sections. Create a nested project only when it has an
independently useful lifecycle. Put details that belong to the parent in a
supplemental document.

Do not infer a parent's status or priority from a child or vice versa. Do not
move project directories, generate a tracked status index, or duplicate either
field elsewhere.

## Verify the current slice

Run the repository's full validation gate plus:

```sh
project show <name>
project check
git diff --check
```

Resolve any warning `project check` prints; `project init` refreshes a stale
Projector section in `AGENTS.md` without touching the repository's own text.
Work built from a description has no project, so skip `project show`. In a
repository without `docs/projects/`, skip `project check` as well. Its
errors there say only that the repository does not use Projector, and
running `project init` to clear them is not part of the change.

Compare the result against the plan's acceptance criteria, or against the
description for work without a plan. If required work on a project
remains, record the exact state, leave the status `in-progress`, and set the
priority to `now`, `next`, or `later` as the user's real scheduling intent
requires. If every criterion in the plan is proven, continue with `finish`
in the same implementation change.

## Hand the work over

These are the defaults where the repository's instructions are silent. Where
its written rules or pull request template say otherwise, follow them — except
that the merge stays the user's checkpoint either way.

Open a pull request and never merge it. The merge is the user's checkpoint,
however small the change: end at `gh pr create --draft`, or
`gh stack submit --auto` for a stack, and hand over the URL.

Size the pull request for its reviewer, not for the plan. A review costs
about the same whether the diff is twenty lines or three hundred, and that
cost is charged per pull request and per review cycle, so combine by default
and make splitting the thing that needs an argument. Fold a follow-on — a
move, a documentation sweep, the retirement this change made possible — into
the pull request that unblocked it, because none of those is worth anything
alone. Split only at the reviewability ceiling: when a reviewer would be
holding two unrelated arguments at once, or when one half would still be
worth shipping if the other were abandoned. Work that crosses it becomes a
stack, as `../gh-stack/SKILL.md` describes.

Open it as a draft. A review loop marks it ready on a clean head, so the
draft says the work has not been signed off yet; never mark it ready
yourself.

Write the title, the body, and each commit message to `../writing.md`,
§ 10 in particular. Write an imperative title and a body that explains why
the change exists, because a squash merge makes the body the commit message.
End every body with
a `## Testing` section: the exact commands, in order, that you ran yourself
from the directory you name; what the reader should see; the signal that
would show the change is wrong; and what needs building first and what state
the commands leave behind. Where a change cannot be exercised by hand, say so
and point at the test that covers it.
