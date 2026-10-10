<!-- projector:begin 2 -->
## Projector conventions

This repository keeps its project plans in Git with
[Projector](https://github.com/ninjudd/projector). Read
`{projects_dir}/README.md` before you change a plan. Use the `project` command
to find and change projects instead of parsing plan files yourself, and run
`project check` before you hand work over.

Write new and substantially revised documentation in the Google developer
documentation style: second person, present tense, active voice, sentence-case
headings, exact identifiers in code font, and a command or example when it
communicates more directly than prose. Keep `docs/` describing the behavior on
the current branch, and update it in the change that alters that behavior. An
existing document keeps its register until you revise it substantially.

Write GitHub issues and review comments in the same style. Write a pull request
body as prose that explains why the change exists, because a squash merge makes
it the commit message. Match the line wrapping already used around a Markdown
edit, and do not hard-wrap prose you post to GitHub, because GitHub renders
line breaks.

Open dependent work that is too large to review as one pull request as a
GitHub stack with `gh stack`, as Projector's `implement` skill describes
under "Stack dependent work". Fix stacked code on the branch that introduced
it, and let `gh stack` carry the fix up by rebasing the layers above. Chain
pull requests by base branch, and update them by merging, only where
`gh stack` is unavailable.

Projector generates this section. To change it, change the template in
Projector and run `project init`.
<!-- projector:end -->
