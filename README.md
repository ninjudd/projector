# Projector

Projector is a Git-native framework for getting work done in any repository.
It gives every project a permanent plan under `docs/projects/`, derives status
and priority views from frontmatter, and supplies one CLI for people and coding
agents.

Projects never move when their status or priority changes. Two branches working
on different projects therefore edit different files instead of contending on a
shared `now.md`, `next.md`, or `later.md` queue.

## Install the CLI

Projector requires Python 3.11 or newer. Install an isolated executable with
`pipx` after the first Projector release reaches the default branch:

```sh
pipx install git+https://github.com/ninjudd/projector.git
project --help
```

The framework is Projector; the command it installs is `project`. The
repository, the `projector-cli` distribution, the Python package, and the
`projector@projector` host plugin all keep the longer name.

For local development, install the checkout in editable mode:

```sh
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/project --help
```

## Install the agent workflows

Projector packages the same canonical skills for Claude Code and Codex. Add the
repository as a marketplace and install the plugin for either host:

```sh
claude plugin marketplace add ninjudd/projector --scope user
claude plugin install projector@projector --scope user

codex plugin marketplace add ninjudd/projector
codex plugin add projector@projector
```

When you work from a clone, install the CLI and both plugins together:

```sh
git clone https://github.com/ninjudd/projector.git
cd projector
./install.sh all
```

`pipx` installs a copy, so pulling new commits does not update the command.
`project upgrade` runs that same installer from any directory:
`project upgrade all` is `./install.sh all`.

The installer removes only legacy symlinks that point from the host's old
agent-config locations into this checkout. It does not replace configuration
directories or touch user-owned files. Run `./install.sh status` before an
upgrade to inspect those paths.

The plugin provides `plan-project`, `work-project`, `finish-project`,
`start-review-loop`, and `start-fix-loop`. Claude invokes a plugin skill as
`/projector:<skill>`; Codex invokes it as `$<skill>`. The core workflows use
the local CLI and do not require MCP.

## Adopt Projector in a repository

Run `init` from anywhere inside a Git repository:

```sh
project init
project create cool-new-feature --status ready --priority next --no-edit
project check
```

This creates the convention at `docs/projects/README.md` and the project plan
at `docs/projects/cool-new-feature/readme.md`. A project can contain supporting
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

## Use the CLI

```sh
project list [--status <status>] [--priority now|next|later] [--json]
project show <project> [--json]
project search <query> [--status <status>] [--priority <priority>] [--json]
project create <project> [--status draft] [--priority later] [--parent <project>]
project edit <project>
project status <project> draft|ready|in-progress|completed
project priority <project> now|next|later
project done <project>
project check [--json]
project config get <key> [--default <value>] [--json]
project upgrade [all|cli|claude|codex|status]
```

Use `--json` when an agent or script consumes output. Every JSON response has
`"schema_version": 2`; diagnostics go to stderr. See [the CLI
reference](docs/cli.md), [the plugin guide](docs/plugins.md), and [the project
convention](docs/projects/README.md) for the complete contracts.

The legacy `install.sh` still installs the pre-Projector agent configuration.
Use the CLI installation above for this layer; native Claude and Codex plugin
installation replaces the legacy script in the workflow layer.

## Develop Projector

Run the validation gate from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -v
PYTHONPATH=src python3 -m projector check
claude plugin validate .
claude plugin validate skills
git diff --check
```
