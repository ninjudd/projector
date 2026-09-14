---
name: work-project
description: Implement a Projector project while keeping its plan and status current. Use when the user asks to start, continue, or complete work recorded under docs/projects.
---

# Work Project

Use the project plan as the durable statement of intent while treating the
repository and current runtime behavior as authoritative evidence.

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
repository's branch, stack, commit, review, and merge rules.

Apply the review method's passes to your own change before the review loop
does; `../start-review-loop/method.md` § 3 describes them, and three apply to
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

Update the project plan in the same change whenever implementation settles a
decision, changes scope, reveals a new constraint, or completes an acceptance
criterion. Append numbered sections rather than renumbering cited sections.
Create a nested project only when it has an independently useful lifecycle;
use a supplemental document for details belonging to the parent.

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

Compare the result against the plan's acceptance criteria. If required work
remains, record the exact state, leave the status `in-progress`, and set the
priority to `now`, `next`, or `later` as the user's real scheduling intent
requires. If every criterion is proven, continue with `finish-project` in the
same implementation change.
