<!-- projector:begin 17 -->
## Projector conventions

Projector generates this section. To change it, change the template in
Projector and run `project init`.

This repository keeps its project plans in Git with
[Projector](https://github.com/ninjudd/projector). Read
`{projects_dir}/README.md` before you change a plan. Use the `project` command
to find and change projects instead of parsing plan files yourself, and run
`project check` before you hand work over.

Write new and substantially revised documentation in the Google developer
documentation style: second person, present tense, active voice, sentence-case
headings, exact identifiers in code font, and a command or example when it
communicates more directly than prose. Keep the living documentation in
`docs/` describing the behavior on the current branch, and update it in the
change that alters that behavior. An existing document keeps its register until
you revise it substantially.

A plan under `{projects_dir}` is not living documentation. Update the plan of
the project you are building as its decisions settle, in the change that
settles them. Run `project done` in the pull request that carries the last of
the project's code, so the project is `completed` when that pull request
merges. Never open a separate pull request just to mark a project completed,
and do not wait for a release: Projector does not track releases, and a
project held open for one tends to stay open. A `completed` plan records what
was decided and built, so leave it as it is, and when later work changes
behavior it describes, update the living documentation instead.

Write GitHub issues and review comments in the same style. Write a pull request
body as prose that explains why the change exists, because a squash merge makes
it the commit message. Match the line wrapping already used around a Markdown
edit, and do not hard-wrap prose you post to GitHub, because GitHub renders
line breaks.

Open dependent work that is too large to review as one pull request as a
GitHub stack with `gh stack`, as Projector's `implement` skill describes
under "Stack dependent work". Fix stacked code on the branch that introduced
it, and let `gh stack` carry the fix up right away by rebasing every layer
above, so each layer stays on the current head of the one below and the
stack can merge as a unit. Chain pull requests by base branch, and update
them by merging, only where `gh stack` is unavailable.

### Projector skills

Follow these rules only when the Projector plugin is installed, which you can
tell because its skills, such as `implement`, are available to you. Without
the plugin, the rest of this section still applies.

Build each change you will open a pull request for with the `implement`
skill, `/projector:implement` in Claude Code or `$implement` in Codex,
whether it follows a plan under `{projects_dir}` or only a description,
however small. Its hand-over opens the pull request and starts a watch loop on
it, so review findings are fixed as they arrive rather than waiting for
someone to notice that nothing is fixing them.

Open every pull request as a draft, each layer of a stack included. A review
loop marks a pull request ready when its head reviews clean, so a draft says
the work has not been signed off yet. Never mark your own draft ready, even
once the reason you drafted it is resolved, because only a clean review
clears it.

Talk with the person in the conversation as usual. When Projector's `ask`
and `tell` tools are available, also post anything they must not miss to
the inbox, a decision with `ask` and news with `tell`, and say it in your
reply too. Always post it when it sits among a lot of other text, such as a
decision in the middle of a long report, where the person could skim past
it. Post news they need to act on, such as a failure that needs them, and
anything that comes up while they are away. Leave out what they already see
elsewhere, such as a pull request's state, which the panel lists. An item
stays above their prompt until they answer or dismiss it, so keep the inbox
for what matters, not as a log of everything you did.

When the person picks one of a question's options, the answer reaches you
as a message that starts `ANSWER`. When they reply to a question you asked
instead, answer what they asked. Then withdraw the question with the
`withdraw` tool if the reply settles it, revise it with the `revise` tool
if the reply shows it was unclear or the choice has changed, or leave it
open while it still needs them. Each time the person writes to you, also
look over the other items you posted that are still open, and withdraw any
that no longer need them, such as news that has gone stale. When the person
says they dismissed an item by mistake, restore it with the `restore` tool or
`project inbox restore` rather than asking again. Name an item to the tools
by the number `ask` or `tell` returned. The panel never shows that number, so
in what you write to the person, name an item by what it asks or tells.
<!-- projector:end -->
