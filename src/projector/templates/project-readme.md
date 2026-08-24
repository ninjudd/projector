# Project plans

Store each project in a permanent directory under `docs/projects/`. Use a
lowercase `readme.md` entry point with YAML frontmatter carrying two fields:
`status: draft|ready|in-progress|completed` records the lifecycle, and
`priority: now|next|later` records when the work should happen. Priority is
required unless the status is `completed`. Nest a project directory inside
another project when the work is a subproject. Keep supplemental files beside
the entry point that owns them.

Status and priority changes edit frontmatter. Do not create shared queue
files, status or priority directories, or symlinks, and do not move a project
when its status or priority changes. Number plan sections and never renumber
them after another document or code comment cites them.

Run `project list` to browse projects and `project check` to validate the
tree.
