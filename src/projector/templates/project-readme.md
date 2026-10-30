# Project plans

This directory follows the [Projector](https://github.com/ninjudd/projector)
convention. Store each project in a permanent directory under `docs/projects/`.
Use a lowercase `readme.md` entry point with YAML frontmatter carrying two
fields: `status: draft|ready|in-progress|completed` records the lifecycle, and
`priority: now|next|later` records when the work should happen. Priority is
required unless the status is `completed`. An optional `owner` names the one
person responsible, preferably by GitHub login. Nest a project directory inside
another project when the work is a subproject. Keep supplemental files beside
the entry point that owns them.

Prefer `project create` to start a project, because it writes the directory,
the entry point, and the frontmatter in this format. Change the frontmatter
with `project status`, `project priority`, `project owner`, and `project done`
rather than by hand. When the frontmatter stops those commands from loading a
plan, fix by hand what the error or `project check` names, then go back to the
commands. Do not create shared queue files, status or priority directories, or
symlinks, and do not move a project when its status or priority changes.
Number plan sections and never renumber them after another document or code
comment cites them. Once a project's work merges into the default branch, its
plan's account of that work records what was decided and built. When later
work changes that behavior, update the living documentation and leave the
plan as it is, unless the later work belongs to the same project: then record
the new decision in a section appended to its plan.

Run `project list` to browse projects, `project search` to search them, and
`project check` to validate the tree. Every `project` command comes from the
Projector CLI:

```sh
pipx install git+https://github.com/ninjudd/projector.git
```
