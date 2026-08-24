# Projects

Use `docs/projects/` to keep project plans with the code they change. Each
project has one permanent directory, and the project status and priority live
in frontmatter. You do not maintain separate queue files or move a project
when its status or priority changes.

## Lay out projects

Put each top-level project directly under `docs/projects/`. Name its entry point
`readme.md`:

```text
docs/projects/
├── README.md
└── cool-new-feature/
    ├── readme.md
    ├── design.md
    └── sub-feature/
        └── readme.md
```

A directory is a project when its `readme.md` begins with Projector status
frontmatter. GitHub renders that file when you browse the directory, while the
frontmatter lets tools distinguish a nested project from a directory that only
organizes supplemental files. Add `design.md`, `decisions.md`, implementation
notes, or other files when the work needs them; none are required.

Use the exact lowercase name. Uppercase `README.md` remains the conventional
entry point for the documentation root and non-project directories; lowercase
`readme.md` identifies a Projector project. `project check` will enforce this
distinction once the CLI ships. Until then, maintain the casing by hand.

The path relative to `docs/projects/` is the project's name. In the example,
the two project names are `cool-new-feature` and
`cool-new-feature/sub-feature`. A nested project belongs to its parent but has
its own status and plan.

Do not add an `all/` directory. Do not create status or priority indexes,
directories, or symlinks. Those structures make status and priority changes
modify shared files or paths. Recursive `readme.md` discovery provides the
index instead.

## Set project status and priority

Begin every `readme.md` with YAML frontmatter containing a `status` field and
a `priority` field:

```yaml
---
status: draft
priority: later
---
```

`status` records where the work is in its lifecycle:

- `draft`: The plan is still being written. It makes no readiness claim.
- `ready`: The plan is complete enough to execute. Every question that blocks
  implementation is answered or deliberately deferred.
- `in-progress`: Implementation has begun and the project is being worked.
  Keep a blocked project here while its blocker is being resolved, and explain
  the blocker in the project plan.
- `completed`: The project no longer needs work. State whether it shipped, was
  abandoned, or was superseded in the project plan.

`priority` records when the work should happen:

- `now`: The project deserves the team's current attention.
- `next`: The project should become current when capacity opens.
- `later`: The project is recorded but not scheduled.

The two fields are independent claims. A `draft` can be `priority: now` when
planning it is the current focus, and an `in-progress` project can drop to
`later` when it is deliberately set aside. Priority is required unless the
status is `completed`; completed work needs no schedule.

Add `owner` only in a repository where more than one person could own the
project. Add other metadata only after a command or workflow needs it. The file
path already supplies the project name, so do not duplicate it in frontmatter.

Change status and priority in the pull request that makes the change true. For
example, the pull request that begins implementation changes the status from
`ready` to `in-progress`. The pull request that completes the final
implementation changes it to `completed`. A stale status is worse than a
missing status.

## Write a project plan

Use numbered sections so code and later documents can cite a stable decision,
for example `docs/projects/cool-new-feature/readme.md § 4`. Do not renumber
existing sections. Add new sections at the end.

Write the smallest plan that makes the intended outcome, constraints,
decisions, verification, and unresolved questions clear. Keep the reason for a
status or priority change in the body rather than adding more field values.
Split material into a supplemental file when it obscures the main plan.

## Find projects before the CLI exists

Projector will provide commands for listing, searching, editing, and validating
projects. Until that CLI ships, use repository-local searches:

```sh
find docs/projects -name readme.md -print
rg -l '^priority: now$' docs/projects -g readme.md
rg -n 'search term' docs/projects
```

These commands generate a view from the project files. They do not write an
index that can become stale or conflict with another branch.

## Rename a project deliberately

Changing status or priority never changes a project path. If the project itself needs a
new name or parent, use `git mv` and update every inbound reference in the same
change. Start the sweep with `rg`, then confirm it with `command grep -rl` so a
binary-classified text file cannot hide a stale reference.
