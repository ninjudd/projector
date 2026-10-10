---
name: implement
description: Implement a Projector project while keeping its plan and status current, or build a change the user describes directly when it needs no spec. Use when the user asks to start, continue, or complete work recorded under docs/projects, or describes a change to build.
---

# Implement

Build from a project's plan, or from the user's description of work small
enough to need no spec. Either way, treat the repository and current
runtime behavior as authoritative evidence.

## Start from a description

When the user describes the work rather than naming a project:

1. Read the repository instructions and run `project list --json`. When a
   project already covers the work, start from its plan instead, as the next
   section describes. When the command exits 66 with `projects directory not
   found`, the repository has not adopted Projector and has no projects.
2. Decide whether the work needs a spec first. It does when it spans several
   parts of the system, changes an interface that other code depends on, or
   leaves a product choice open. Then stop, tell the user why, and suggest
   `spec`. Build it without a spec only when the user asks you to.
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
does; `../review-pr/method.md` § 3 describes them, and three apply to
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

Follow `../guidelines.md`, the code guidelines every Projector skill shares.
The review loop reads your head against them and the fix loop follows them in
each fix, so a rule skipped here comes back as a finding citing the section
you skipped. Its first rule is about comments: write code that needs none,
simplify a stretch before you explain it, and add a comment only to preserve a
constraint or reason the code cannot carry on its own.

When the work has a plan, update it in the same change whenever
implementation settles a decision, changes scope, reveals a new constraint,
or completes an acceptance criterion. Follow the writing guide in
`../writing.md` when you write the update, as `spec` does. Append numbered
sections rather than renumbering cited sections. Create a nested project
only when it has an independently useful lifecycle. Put details that belong
to the parent in a supplemental document.

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
repository where `project list` found no projects directory, skip
`project check` as well. Its errors there say only that the repository does
not use Projector, and running `project init` to clear them is not part of
the change.

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
stack, built as [Stack dependent work](#stack-dependent-work) describes.

Open it as a draft. A review loop marks it ready on a clean head, so the
draft says the work has not been signed off yet; never mark it ready
yourself.

Follow the writing guide in `../writing.md`, § 10 in particular, when you
write each commit message, the title, and the body. Its first rule starts
before your first commit: read the default branch's recent subjects and
write every commit subject, and later the title, in the pattern they
follow. Before you hand over the URL, read each title back against those
subjects. `gh stack submit` writes a layer's title itself, so retitle one
that falls outside the pattern with `gh pr edit <number> --title`. When
you delegate the pull request to a subagent, pass it the pattern you
found.

Write a body that explains why the change exists, because a squash merge
makes the body the commit message. End every body with a `## Testing`
section: the exact commands, in order, that you ran yourself from the
directory you name; what the reader should see; the signal that would show
the change is wrong; and what needs building first and what state the
commands leave behind. Where a change cannot be exercised by hand, say so
and point at the test that covers it. `gh stack submit` writes each layer's
body too, and it is the wrong body; replace it as rule 3 of
`../gh-stack/SKILL.md` describes.

## Stack dependent work

A stack is a chain of pull requests, each based on the branch of the one
below it, that GitHub records as one stack. Make and keep one with
`gh stack`, the GitHub CLI extension that `../gh-stack/SKILL.md` describes;
that file is the reference for every `gh stack` command named here. `fix-pr`
and `start-fix-loop` find and update a stack's layers, and fall back when
`gh stack` cannot help, as this section describes.

- **Create it.** Plan the layers from the bottom up, make them with
  `gh stack init` and `gh stack add`, and open them all as drafts with
  `gh stack submit --auto`. Pull requests you chain yourself with
  `gh pr create --base` look like a stack, but GitHub does not record them
  as one, so open no layer that way while `gh stack` works.
- **Adopt a chain.** Make pull requests already chained by base branch into
  a stack with `gh stack link` and their numbers, bottom first.
- **Carry a fix up it.** After a fix on a lower layer, run
  `gh stack rebase --upstack` and then `gh stack push` from that layer, and
  after a layer merges, run `gh stack sync`. In a checkout that does not
  track the stack yet, `gh stack checkout <number>` sets it up from GitHub.
- **Update it by rebasing.** `gh stack` keeps a stack current by rebasing
  its layers and pushing each branch with `--force-with-lease`, and it has
  no option to merge instead. Inside a stack, that rebase is how a layer
  takes in the layer below it and the default branch alike: when a layer
  falls behind or conflicts with its base, run `gh stack rebase` and then
  `gh stack push`, and resolve a conflict the rebase stops on as
  `../gh-stack/SKILL.md` describes under "Handle rebase conflicts". Never
  merge a base into a layer of a stack `gh stack` works on.
- **Find its layers.** Ask GitHub which stack holds a pull request:

  ```sh
  gh api "repos/<owner>/<repo>/stacks?pull_request=<number>"
  ```

  It returns the stack with its pull requests from the bottom up, or `[]`
  when the pull request is in none, and it needs neither a checkout nor the
  extension. In a checkout that tracks the stack, `gh stack view --json`
  lists the same layers with their branches.

Fall back to base branches whenever `gh stack` cannot answer, and keep
working. That is when `gh extension list` shows no `github/gh-stack`, when a
`gh stack` command exits 9 because the repository does not have stacks
enabled, or when the stacks API returns an error or `[]`. Then work from the
pull requests' base branches:

- **Find its layers** by following base branches. The layer below a pull
  request is the open pull request whose head branch is its base branch,
  from `gh pr list --head <baseRefName> --json number`, and the chain ends
  at the default branch. The layers above it are the open pull requests
  based on its head branch, from
  `gh pr list --base <headRefName> --json number`.
- **Create it** from the bottom up when `gh stack` is missing or exits 9:
  push each layer's branch with `git push -u origin <branch>`, and open it
  with `gh pr create --draft --base <branch of the layer below> --head <branch>`.
- **Update it by merging**, as `fix-pr` updates any branch: carry a fix up
  by merging each layer into the one above it, from the bottom up, and take
  in the default branch by merging it into the bottom layer and carrying
  that up the same way. A merge needs no force-push, so review threads stay
  anchored to their commits.

Tell the user at most once why the chain is not a stack: that
`gh extension install github/gh-stack` would make it one when the extension
is missing, or that the repository does not have stacks enabled when
`gh stack` exits 9. A pull request that is simply in no stack needs no note.
Never install the extension yourself.
