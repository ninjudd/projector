# Projector contributor instructions

Projector is a public, portable framework. Keep repository behavior independent
of personal account names, home-directory layouts, machine-local tools, and one
host's private configuration.

## Preserve the project model

Read `docs/projects/README.md` before changing project plans.

- Store each project at `docs/projects/<name>/readme.md`. The lowercase
  filename is the project sentinel.
- Derive the canonical name from the path relative to `docs/projects/`.
- Use only `draft`, `ready`, `in-progress`, or `completed` in `status`
  frontmatter, and only `now`, `next`, or `later` in `priority`. Priority is
  required unless the status is `completed`.
- Treat `ready` as the executable-readiness claim. A plan at `ready` or
  `in-progress` must answer or deliberately defer every question that blocks
  implementation. Priority makes no readiness claim.
- Start a project with `project create`. Change its status, priority, and
  owner with `project status`, `project priority`, `project done`, and
  `project owner`, never by editing the frontmatter. The one exception is a
  plan those commands cannot load: fix by hand what the error or
  `project check` names, then go back to the commands. Never move a project,
  create a shared queue file, or generate a tracked index.
- Allow supplemental files and recursively nested projects. A directory is a
  project only when it has its own lowercase `readme.md`.
- Number plan sections and append new sections without renumbering cited
  sections.
- Keep status and priority current in the same pull request that changes what
  they claim. Mark a finished project `completed` and record whether it
  shipped, was abandoned, or was superseded.

Use `project` for project discovery and mutation rather than duplicating
frontmatter parsing in a skill:

```sh
project list --json
project search <query> --json
project show <name> --json
project create <name>
project status <name> <status>
project priority <name> <priority>
project owner <name> <login>
project owner <name> --clear
project done <name>
project check
project config get <key>
project config set <key> <value>
```

## Keep the core portable

The Python CLI has no runtime dependencies and supports Python 3.11 or newer.
Prefer the standard library unless a dependency has a concrete cross-platform
benefit that justifies its installation and release cost.

The root `skills/` directory is the single source for both hosts.
`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` package that
same tree. Do not copy or generate host-specific skill forks. Put a genuinely
host-specific enhancement in host packaging and keep the shared workflow
usable without it.

MCP is optional. Do not make the CLI or a core skill depend on an MCP server.
Add a server only when structured remote tools, authentication, or persistent
infrastructure creates value beyond the local CLI.

Skills must obtain repository policy and GitHub identities from explicit user
input, repository instructions, or user configuration. Never add a personal
default to a public skill. Preserve authorization boundaries: visibility into
a repository, branch, review, or account does not authorize mutation.

## Run the validation gate

Run the full gate from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -v
PYTHONPATH=src python3 -m projector check
claude plugin validate .
claude plugin validate skills
claude plugin test .
hooks/typecheck.sh
git diff --check
```

The packaging tests validate both plugin manifests and their exact published
skill set. `claude plugin validate .` validates the marketplace and the Claude
Code hooks module in `hooks/`, while `claude plugin validate skills` validates
the skill tree. `claude plugin test .` runs the hooks module's `*.test.ts`
and `*.test.tsx` files. Codex has no separate public validation command.

`hooks/typecheck.sh` type-checks the hooks module with `site/`'s TypeScript,
through the root `tsconfig.json`. It compiles against the declarations
Claude Code writes to `.claude-plugin/types/` each time an interactive
session loads the checkout as a plugin. Nothing else provides them, so CI
cannot run this check. Once per checkout, run `npm ci` in `site/`, and start
a session with `claude --plugin-dir .` from the repository root. In a linked
worktree that has no `tsc` or no declarations of its own, the script uses the
main checkout's and says so. The declarations depend mainly on the Claude
Code version, so a worktree can share them. The script fails with the setup
instruction when neither checkout has what it needs. Start such a session
again after you update Claude Code, so the declarations match it.

The site's front end lives in `site/`: TypeScript under `site/src/`, and
the stylesheets, icon, and compiled JavaScript the site serves under
`site/assets/`, which the wheel ships as `projector.site.assets`. Commit the
compiled files with the change that alters them, because every repository's
deploy and every CLI install reads them from a Git tree. Run npm in `site/`.
After editing a `.ts` file, run `npm ci` once, then `npm run check`, which
type-checks and lints with typescript-eslint's strict type-aware rules and
allows no warnings, and `npm run build`. Fix a finding in the code rather
than suppressing it or loosening the configuration. The test suite compiles
the TypeScript and fails when the committed JavaScript differs; it skips that
check where `npm ci` has not run in `site/`. Resolve a conflict in a compiled
file by taking either side and running `npm run build`, never by hand.

Test mutation behavior in temporary Git repositories. Include exact casing,
nested projects, dirty-file preservation, ambiguous input, and binary
NUL-containing reference fixtures where relevant.

## Hand work over for review

Open pull requests; never merge them. The user owns the merge checkpoint.
Keep tests and documentation with the code they verify.

Open your own pull request as a draft, and draft every layer of a stack you
open. A review loop marks a pull request ready on a clean head, so the draft
says the work has not been signed off yet and the ready transition says it
has — a self-review landing before the work reaches human eyes.

Never mark your own draft ready yourself; a clean review is what clears it.
The tempting move is undrafting once the reason you drafted is resolved — a
red test now passing, say — which sends the work to human reviewers carrying
a sign-off nothing gave it.

Use an imperative pull request title and a body that explains why the change
exists. End every body with a `## Testing` section containing commands you ran
from the directory you name, the expected result, the failure signal, and any
state the commands leave behind. Do not put stack scaffolding in the body
because squash merges preserve that body as the commit message.

For review findings, verify first, commit locally, reply with the commit, push,
resolve the thread, and re-fetch it in that order.

<!-- projector:begin 10 -->
## Projector conventions

Projector generates this section. To change it, change the template in
Projector and run `project init`.

This repository keeps its project plans in Git with
[Projector](https://github.com/ninjudd/projector). Read
`docs/projects/README.md` before you change a plan. Use the `project` command
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
whether it follows a plan under `docs/projects` or only a description,
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
elsewhere, such as a pull request's state, which the band lists. An item
stays above their prompt until they answer or dismiss it, so keep the inbox
for what matters, not as a log of everything you did. Each time the person
writes to you, look over the items you posted that are still open, and
withdraw any that no longer need them, such as a question they answered in
the conversation or news that has gone stale, with the `withdraw` tool and
the number `ask` or `tell` returned.
<!-- projector:end -->
