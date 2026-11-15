---
name: spec
description: Write a project's technical spec as a Git-native plan under docs/projects, or refine an existing one. Use when the user wants to scope, specify, design, prioritize, or record a project before or alongside implementation.
---

# Spec a project

Turn the requested outcome into a durable Projector plan, a technical spec
that another person or agent can execute without reconstructing the
conversation. Work small enough to need no spec goes straight to
`implement`, which builds from a description as well as from a plan.

## Establish context

1. Read the repository instructions and `docs/projects/README.md`.
2. Run `project list --json` and search for overlapping or parent projects.
3. Inspect the code and current documentation that constrain the work. Do not
   design from the request alone when the repository can answer a question.
4. Resolve the intended canonical name and whether this is a top-level or
   nested project. Ask only when different answers would materially change the
   project.

## Write the plan

Use `project create <name> --status <status> --priority <priority> --no-edit`,
or inspect the existing project with `project show <name> --json`, edit its
plan content, and change the lifecycle with `project status <name> <status>`
or the schedule with `project priority <name> <priority>`.

Choose the status from how finished the plan is:

- Use `draft` while the plan is still being written or still has questions
  that block implementation.
- Use `ready` once the plan can be executed as written.
- Leave the transition to `in-progress` to `implement`, which keeps that
  claim with the implementation pull request.
- Do not create a new plan as `completed`.

Choose the priority from the user's scheduling intent, independently of the
status:

- Use `now` when the project deserves current attention, including when
  planning it is itself the current work.
- Use `next` when it should become current as capacity opens.
- Use `later` for recorded but unscheduled work.

### Lead with the problem, then the solution

The plan's reader is the person or agent who implements or reviews it. They
may never have seen this conversation or this part of the code, and they
need two things before anything else: what is wrong, and what the change
is. Give them both on the first screen, in this order.

1. **The title and a one-sentence summary.** Under the title, say in one
   sentence what the project makes true.
2. **Section 1, the problem.** Keep it short, and write it for someone new
   to the area. Say what the existing system does and why it exists, what
   goes wrong today and through which mechanism, and how much it matters,
   with one or two pieces of evidence. The rest of the evidence goes in the
   background section.
3. **Section 2, the solution.** Open with one paragraph that states the
   change whole, so a reader who stops there knows the fix. Then describe
   it concretely, as one unit, in subsections: the runtime flow after the
   change as a numbered sequence, each new or changed data shape as a table
   of fields and values, and a table that maps each file or component to
   its change. The solution holds every piece of the design. A design
   spread across an outcome, a list of decisions, and an implementation
   sequence makes the reader assemble it.

Add the sections below after those two, each only when it has content, in
this order.

- **Why this design.** Rationale only: why the solution has this shape, and
  which alternatives were rejected and why. Name the existing code the
  solution extends rather than duplicates, the logic more than one part of
  the change needs so it is built once, and for anything the change
  replaces, what is removed and every caller that moves to the replacement.
  The solution reads whole without this section, and this section repeats
  none of it.
- **Acceptance criteria.** The evidence that proves the project complete:
  the behavior that must hold, the inputs and states that could break it,
  and the test or observation that proves it. A table of what to do and
  what to expect, followed by the tests that fail before the change, works
  well. Name the bug each test catches, as `../testing.md` § 1 asks, and pin a
  value through what depends on it (§ 3), so the plan never asks for a test
  that restates the code. A criterion nobody can observe is a hope, not
  evidence. Write criteria the merged code can meet, because the project is
  complete once its code merges; a release that follows is tracked outside
  Projector.
- **Cost.** The paths where cost matters, the load they carry, and how the
  change is measured against it, or that cost does not matter here and
  why. A plan silent on cost leaves the implementer and the reviewer to
  guess which paths are hot.
- **Rollout.** A numbered sequence only for steps that depend on each other
  or that happen in order across releases, environments, or flags. When the
  order of the code changes does not matter, the solution's file table
  already says what to change, so write no implementation sequence that
  repeats it.
- **Open questions.** Each with an owner or a deliberate deferral.
- **Background.** The evidence and the code archaeology behind the problem
  and the solution: logs, counts, queries, and what you found when you read
  the code. It comes after the design, never before it. A reader who trusts
  the problem statement never needs it, and a reader who doubts it finds it
  here.

Those sections give the three things a review checks on every head
their places: correctness in the acceptance criteria, performance in the
cost section, and reuse and replacement among the reasons for the design.
Settle each there, so the implementation gives the review nothing to find.

Keep the plan proportional to the work. A plan for one new branch and one
new record fits on a few screens. One that runs to hundreds of lines is
describing the design more than once or carrying background the reader did
not ask for. Add supporting files only when they hold real content that
would make the entry point unwieldy.

An existing plan keeps its section numbers, and each numbered section keeps
its subject, because code and other documents cite both. A citation that
meant the acceptance criteria must not land on the solution after the plan
is reshaped. Bring the problem and the solution to the top of such a plan in
the unnumbered lead under the title: the one-sentence summary, a short
paragraph that states the problem, and a paragraph that states the fix
whole. Leave the design where its numbered sections already describe it, and
add a full new section only at the end, with the next number.

Follow the writing guide in `../writing.md` when you write the plan. State
each decision and its reason rather than pointing at where it was discussed
(`writing.md` § 2). The problem and the solution describe the system, the
reasons for the design explain it, and a rollout is a how-to, so keep each
to its job (§ 3). Put steps, alternatives, criteria, fields, and file
changes in numbered lists, bulleted lists, and tables (§ 4), and write to
the reader (§ 5).

Number sections and append new sections without renumbering existing ones.
Write paths and identifiers exactly. Keep durable decisions in the plan rather
than relying on chat history.

## Validate and hand over

Run:

```sh
project show <name>
project check
git diff --check
```

Resolve any warning `project check` prints; `project init` refreshes a stale
Projector section in `AGENTS.md` without touching the repository's own text.

Then read the plan as its reviewer would, and confirm each of these:

- A reviewer can say what the problem is and what the fix is after the first
  screen: the summary, the problem, and the paragraph that opens the
  solution. If they would have to read further, move the design up, or
  state it in the lead of an existing plan.
- The plan is proportional. It describes the design once, and the
  background follows the design rather than leading to it.
- The status makes an honest readiness claim, and the priority matches the
  user's real scheduling intent.
- The acceptance criteria are observable.
- The plan says what it reuses, replaces, and measures, or why cost does not
  matter.
- Every open question has an owner or a deliberate deferral.

### Open the pull request

These are the defaults where the repository's instructions are silent.
Where its written rules or the user say otherwise, follow them, except that
the merge stays the user's checkpoint either way.

Commit the plan on a branch, never on the default branch, and check the
whole branch before you push it:

```sh
git diff --check origin/<default-branch>...HEAD
```

The bare `git diff --check` above reads only tracked changes in the working
tree, so it misses a new plan file and anything already committed. The
range form reads the whole branch. Then push the branch. When it already
has an open pull request, such as an earlier spec's pull request for the
plan you are refining, push to that pull request and open no other.

Otherwise, open the pull request with `gh pr create --draft`, and never
merge it. Follow the writing guide in `../writing.md`, § 10 in particular:
write the title in the pattern of the default branch's recent subjects, and
a body that explains why the plan exists. End the body with a `## Testing`
section that lists these commands, what each prints, and the output that
would show the plan is wrong:

```sh
project show <name>
project check
git diff --check origin/<default-branch>...HEAD
```

Then subscribe the session to fix the pull request, so review findings on
the plan are fixed as they arrive:

```sh
project watch add <owner>/<repo>#<number>
```

Subscribe as `../fix-pr/SKILL.md` describes under "Subscribe the session to
the pull request": skip the command when
`project config get watch.auto_start --default true` prints `false`, and act
on the `events:` line it prints.

### Ask the user to review the plan

A plan's decisions are the user's, and their feedback often changes the
design itself, so the user reads the plan before any automated review does.
Keep the pull request a draft until they answer, because marking it ready
is what starts that review. When the user asks for the automated review to
go first, mark the pull request ready at the hand-over instead, and still
ask them to review the plan.

When Projector's `ask` and `request` tools are available:

1. **Settle each blocking question.** For each open question that blocks
   implementation and needs the user's decision, post an `ask` with the
   answer you recommend, and link the question's section on the rendered
   plan in its detail. As each answer arrives, record the decision in the
   plan, move the status with `project status` once its readiness claim is
   honest, and push.
2. **Post one review request.** Once no blocking question waits on the
   user, post one `request` for the pull request you opened. Post none for
   a pull request you only pushed a refinement to, because its own session
   holds its draft state. Read the link's owner, repository, and branch from
   `gh pr view <number> --json headRepositoryOwner,headRepository,headRefName`,
   so the link names a fork's branch correctly, and post the request only
   after your push, so the link never names a branch that GitHub does not
   have.

   | Field | Value |
   | --- | --- |
   | `request` | The project and what it makes true, in a few words, such as `Review the spec-handover plan: specs end with a review request`. |
   | `detail` | `Spec:` and `https://github.com/<owner>/<repo>/blob/<branch>/<path>/readme.md`, then `PR:` and the pull request's URL. Then a numbered list of the sections to read, each with what the user should find there, so their notes can name the section they are about. |
   | `options` | `Looks good`, `Some feedback`. |
   | `start` | None. There is nothing to prepare. |
   | `pr` | `<owner>/<repo>#<number>`. |

3. **Act on the answer.**

   | Answer | What you do |
   | --- | --- |
   | `Looks good` | Run `project review ready <number> --repo <owner>/<repo>`. |
   | `Some feedback` | Revise the plan from the notes, push, and update the same request with the `revise` tool, which reopens it. Keep the pull request a draft. |
   | Declined | Run `project review ready <number> --repo <owner>/<repo>`. A decline is the user's no to reviewing the plan in the inbox, not to the plan, and a draft that nobody marks ready can never merge. |
   | A reply in their own words | Answer it, and leave the request open. |

   The user's approval moves only the pull request's draft state, never the
   plan's `status`. The status is your readiness claim, and step 1 made it
   honest.

Run `project review ready` as a command of its own, never as a bare
`gh pr ready`, as `../fix-pr/SKILL.md` describes under "Hold the pull
request while changes are built".

Where the inbox tools are not available, raise each blocking question in
the conversation instead, and mark the pull request ready at the hand-over,
because no inbox answer would ever arrive to do it.

When you hand over, give the pull request's URL and the rendered plan's
URL, say whether a review request waits in the inbox, and say whether the
session is subscribed to fix the pull request and how its events arrive.
