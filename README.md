# Projector

Projector is a Git-native framework for getting work done in any repository.
It gives every project a permanent plan under `docs/projects/`, derives status
and priority views from frontmatter, and supplies one CLI for people and coding
agents.

Projects never move when their status or priority changes. Two branches working
on different projects therefore edit different files instead of contending on a
shared `now.md`, `next.md`, or `later.md` queue.

## Install

One command installs the `project` command from the newest release and the
plugin for Claude Code and Codex, whichever you have. It needs Python 3.11 or
newer, and no git:

```sh
curl -fsSL https://projector.bot/install.sh | bash
```

`project upgrade` moves everything to the newest release later. To install
only the CLI, pin a release, or install each plugin yourself, see
[Install Projector](docs/plugins.md).

## Get started

Run `init` from anywhere inside a Git repository:

```sh
project init
project create cool-new-feature --status ready --priority next --no-edit
project check
```

`init` writes the convention to `docs/projects/README.md` and Projector's
conventions into `AGENTS.md`, linked as `CLAUDE.md`, so every agent session in
the repository follows them. When `origin` is on GitHub, it also sets up the
[Projector site](docs/site.md). `create` writes the plan at
`docs/projects/cool-new-feature/readme.md`. A project can hold supporting
documents and nested projects:

```text
docs/projects/cool-new-feature/
├── readme.md
├── design.md
└── sub-feature/
    └── readme.md
```

Each project entry point carries two independent fields. `status` is the
lifecycle: `draft`, `ready`, `in-progress`, or `completed`. `priority` is the
schedule: `now`, `next`, or `later`. Run `project list` to group projects at
query time. Projector never writes a tracked status index.

## Work with coding agents

The plugin gives Claude Code and Codex the same skills: `plan`, `implement`,
and `finish` a project; `start-review-loop` and `start-fix-loop` to review and
fix pull requests continuously; and `walkthrough-pr`, which builds a guided
walkthrough for reviewing a large pull request. Claude invokes a skill as
`/projector:<skill>` and Codex as `$<skill>`. See
[Install Projector](docs/plugins.md) for how the skills work together.

## Publish a site

Every repository can serve a site on GitHub Pages with its projects, its pull
request walkthroughs, and its docs. `project init` sets it up, and
`project site serve` shows it locally without Pages. See
[Set up the Projector site](docs/site.md).

## Use the CLI

```sh
project list [--status <status>] [--priority now|next|later]
project show <project>
project create <project> [--status draft] [--priority later]
project status <project> draft|ready|in-progress|completed
project priority <project> now|next|later
project done <project>
project check
project upgrade
```

Add `--json` when an agent or script consumes output. See
[the CLI reference](docs/cli.md) for every command, and
[the project convention](docs/projects/README.md) for how plans are laid out.

## Develop Projector

See [Develop Projector](docs/develop.md) for running the CLI from a clone,
trying unreleased skills, the validation gate, and releases.
