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
  well. A criterion nobody can observe is a hope, not evidence.
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

Those sections give the three things the review loop checks on every head
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

Leave the plan changes visible for ordinary Git review; do not commit, push,
or open a pull request unless the user or repository workflow asks for those
actions.
