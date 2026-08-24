---
name: plan-project
description: Create or refine a Git-native project plan under docs/projects. Use when the user wants to scope, design, prioritize, or record a project before or alongside implementation.
---

# Plan Project

Turn the requested outcome into a durable Projector plan that another person or
agent can execute without reconstructing the conversation.

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
- Leave the transition to `in-progress` to `work-project`, which keeps that
  claim with the implementation pull request.
- Do not create a new plan as `completed`.

Choose the priority from the user's scheduling intent, independently of the
status:

- Use `now` when the project deserves current attention, including when
  planning it is itself the current work.
- Use `next` when it should become current as capacity opens.
- Use `later` for recorded but unscheduled work.

Keep the plan proportional to the work. State the outcome, constraints,
acceptance evidence, implementation sequence, decisions with rejected
alternatives, and genuinely open questions. Add supporting files only when
they hold real content that would make the entry point unwieldy.

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

Confirm that the status makes an honest readiness claim, the priority matches
the user's real scheduling intent, the acceptance criteria are observable, and
every open question has an owner or deliberate deferral. Leave the plan changes visible for ordinary Git review; do not commit,
push, or open a pull request unless the user or repository workflow asks for
those actions.
