<!-- projector:begin 22 -->
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
merges. Never open a separate pull request just to mark a shipped project
completed, and do not wait for a release: Projector does not track releases,
and a project held open for one tends to stay open. A `completed` plan records
what was decided and built, so leave it as it is, and when later work changes
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

Talk with the person in the conversation as usual. When Projector's `ask`,
`tell`, and `request` tools are available, also post anything they must not
miss to the inbox, a decision with `ask`, news with `tell`, and work only
they can do with `request`, and say it in your reply too. Always post it
when it sits among a lot of other text, such as a decision in the middle of
a long report, where the person could skim past it. Post news they need to
act on, such as a failure that needs them, and anything that comes up while
they are away. Leave out what they already see elsewhere, such as a pull
request's state, which the panel lists. An item stays above their prompt
until they answer or dismiss it, so keep the inbox for what matters, not as
a log of everything you did.

Post a request when the next step needs the person to do something you
cannot do yourself, such as running a command you are not allowed to run,
signing in to a service, or checking a change on a device you cannot reach,
like their phone. Ask a question instead when you need their decision, and
do the work yourself whenever you can. Write the request as what they should
do, and put the steps in its detail as a numbered list, each step with what
they should see, so their notes can name the step that failed. Post one
request for the whole hand-off rather than a question and a notice for its
parts, and keep working on anything it does not block. Give the request the
options they choose from when they finish, each a short answer that fits the
work, such as `Looks good` and `Some feedback` for a test; each marks it
done, and they can always decline it instead. Give the request a start
action only when you must prepare something before they can begin, such as
installing a build on their phone, and leave it out otherwise. Do the slow
part before you post the request, such as building the app, so the start
action only installs and launches it and they are not left waiting once they
press start. They press start when they are ready, and a message that starts
`STARTED` arrives quoting the start action. Do the start action only then,
do exactly what that message quotes and nothing more, and mark the request
ready with the `ready` tool, or blocked with the `block` tool and what they
should do first, such as unlocking the phone.

When the person picks one of a question's options, the answer reaches you
as a message that starts `ANSWER`. When they finish a request, its outcome
reaches you as their prompt, quoting the request with the option they chose
and any notes under it, or as a message that starts `DONE` and names the
option. When they decline it, a message that starts `DECLINED` arrives. Read
the option and the notes, and act on what they say before you go on, even
when the request is done. A declined request is the person's no, so do not
post it again unless they ask. When they reply to a question or a request
instead, answer what they asked. Then withdraw the item with the `withdraw`
tool if the reply settles it, revise it with the `revise` tool if the reply
shows it was unclear or the choice or the work has changed, or leave it open
while it still needs them. Each time the person writes to you, also look
over the other items you posted that are still open, and withdraw any that
no longer need them, such as news that has gone stale. When the person says
they dismissed or declined an item by mistake, restore it with the `restore`
tool or `project inbox restore` rather than posting it again. Name an item
to the tools by the number `ask`, `tell`, or `request` returned. The panel
never shows that number, so in what you write to the person, name an item
by what it asks, tells, or requests.
<!-- projector:end -->
