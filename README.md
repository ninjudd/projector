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

`pipx` installs a copy and each host caches the plugin, so pulling new
commits updates neither. Running the installer again upgrades both, and
`project upgrade` runs it from any directory: `project upgrade all` is
`./install.sh all`. Either one ends by saying whether the checkout is behind
its upstream, because the command is built from the checkout while a GitHub
marketplace serves the plugin.

The installer removes only legacy symlinks that point from the host's old
agent-config locations into this checkout. It does not replace configuration
directories or touch user-owned files. Run `./install.sh status` before an
upgrade to inspect those paths.

The plugin provides `plan-project`, `implement`, `finish-project`,
`start-review-loop`, `start-fix-loop`, and `walkthrough-pr`, which builds a
guided walkthrough page for reviewing a large pull request. Claude invokes a
plugin skill as `/projector:<skill>`; Codex invokes it as `$<skill>`. The
review loop inspects each head by the method in
`skills/start-review-loop/method.md`, and the fix loop verifies findings by
the same protocol. All three of `implement`, the review loop, and the fix
loop share the code guidelines in `skills/guidelines.md`, so the rules one
writes to are the rules the others review and fix against. The core
workflows use the local CLI and do not require MCP.

## Adopt Projector in a repository

Run `init` from anywhere inside a Git repository:

```sh
project init
project create cool-new-feature --status ready --priority next --no-edit
project check
```

This creates the convention at `docs/projects/README.md` and the project plan
at `docs/projects/cool-new-feature/readme.md`. It also writes Projector's
conventions into a marked section of `AGENTS.md`, linked as `CLAUDE.md` so
Claude Code reads the same file, and every agent session in the repository
reads them whether or not a Projector skill is loaded. Run `init` again to
refresh that section when `check` says it is outdated. A project can contain
supporting documents and nested projects:

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

## Set up the Projector site

Projector can host a site for a repository on GitHub Pages, built from content
Projector keeps in the repository itself. Its home page renders the
repository's `README.md`, and its menu reaches the project plans under
`docs/projects`, the pull request walkthroughs the `walkthrough-pr` skill
publishes, and every other Markdown document under `docs/`. Walkthroughs sit
on hidden refs such as `refs/projector/walkthroughs`, which are not branches,
so publishing one adds no branch, no pull request banner, and nothing to
anyone's clone. One workflow on the default branch builds and deploys the
site when a walkthrough is published or `README.md` or `docs/` changes. Set a
repository up once, with admin rights, from a checkout of it; `gh` fills in
`{owner}` and `{repo}` from the checkout's remote:

1. Turn on Pages with GitHub Actions as its source:

   ```sh
   gh api -X POST 'repos/{owner}/{repo}/pages' -f build_type=workflow
   ```

   For a repository that already has a Pages site, switch its source to
   GitHub Actions under **Settings > Pages** instead.

2. If the repository is private, make the site private too, and stop here
   if GitHub refuses. A private repository's Pages site is public unless the
   account has private Pages, which needs GitHub Enterprise Cloud:

   ```sh
   gh api -X PUT 'repos/{owner}/{repo}/pages' -F public=false
   ```

3. Add the workflow to the default branch through a pull request. GitHub runs
   the dispatched workflow only from the default branch, and deploys from the
   default branch without any change to the `github-pages` environment. Save
   this as `.github/workflows/projector-site.yml`, or have the Projector CLI
   write it with `project site workflow --write`:

   ```yaml
   name: Projector site
   on:
     repository_dispatch:
       types: [projector-walkthroughs]
     push:
       branches: [main]
       paths: [README.md, 'docs/**']
     workflow_dispatch:
   permissions:
     contents: read
     pull-requests: read
     pages: write
     id-token: write
   concurrency:
     group: pages
     cancel-in-progress: false
   jobs:
     publish:
       runs-on: ubuntu-latest
       environment:
         name: github-pages
         url: ${{ steps.walkthroughs.outputs.page_url }}
       steps:
         - id: walkthroughs
           uses: ninjudd/projector/actions/walkthroughs@v0
   ```

   `@v0` follows Projector's compatible releases. Pin an exact tag such as
   `@v0.5.0`, or a full commit SHA, to change only when you choose.

4. Publish something. Once the workflow is on the default branch, the
   `walkthrough-pr` skill publishes to the site by default: ask your agent
   for a walkthrough of a pull request and it pushes the spec to the hidden
   ref, starts the workflow, and hands you the link. Ask for a Claude
   Artifact instead when you want a private page. The site's PRs menu lists
   every walkthrough, and each pull request's newest version is at
   `/<number>/`. The README, the plans and the docs appear on the first
   deploy, without publishing anything.

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
