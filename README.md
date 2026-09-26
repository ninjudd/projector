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

The plugin provides `plan`, `implement`, `finish`, `start-review-loop`,
`start-fix-loop`, and `walkthrough-pr`, which builds a guided walkthrough
page for reviewing a large pull request. Claude invokes a
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
reads them whether or not a Projector skill is loaded. When `origin` is on
GitHub, `init` also sets up the [Projector site](#set-up-the-projector-site);
pass `--no-site` to skip it. Run `init` again to refresh that section when
`check` says it is outdated. A project can contain
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
Projector keeps in the repository itself. Its menu has three sections:
**Projects**, the home page, lists the projects under `docs/projects`;
**Reviews** lists the pull request walkthroughs the `walkthrough-pr` skill
publishes; and **Docs** renders `README.md` beside a sidebar of every other
document under `docs/`. A document is Markdown, which the site renders, or an
HTML page, which it shows as it is inside the site, with the files beside it,
so an interactive explorer or a WebAssembly playground works there as it does
from disk. Walkthroughs sit
on hidden refs such as `refs/projector/walkthroughs`, which are not branches,
so publishing one adds no branch, no pull request banner, and nothing to
anyone's clone. One workflow on the default branch builds and deploys the
site when a walkthrough is published or `README.md` or `docs/` changes. Set a
repository up once, with admin rights, from a checkout of it whose `origin`
is the GitHub repository:

1. Run `init`:

   ```sh
   project init
   ```

   Besides adopting the convention, it turns on GitHub Pages with GitHub
   Actions as its source, and points the repository's website link at the
   site if the link is empty. A repository that already deploys its own
   Pages site keeps it unless you pass `--site`. If the repository is private, it makes the site private too, and
   skips the workflow if GitHub refuses: a private repository's Pages site
   is public unless the account has private Pages, which needs GitHub
   Enterprise Cloud. Then it writes the workflow. When it cannot set the site
   up, for example because you are not an admin, it says why on stderr and
   adopts the repository anyway; pass `--site` to make that an error. Run it
   again at any time; it changes only what is out of date.

2. Commit the workflow to the default branch through a pull request. GitHub
   runs the dispatched workflow only from the default branch, and deploys
   from the default branch without any change to the `github-pages`
   environment. `init` writes `.github/workflows/projector-site.yml`,
   which `project site workflow --write` also writes on its own:

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
         url: ${{ steps.site.outputs.page_url }}
       steps:
         - id: site
           uses: ninjudd/projector/actions/site@v0
   ```

   `@v0` follows Projector's compatible releases. Pin an exact tag such as
   `@v0.5.0`, or a full commit SHA, to change only when you choose, with
   `--action-ref`.

   A page the repository generates rather than commits, such as an HTML
   explorer or a WebAssembly module a playground loads, needs building before
   the site. Name the command that builds it as `site.prepare` in
   `.projector.toml`, where the deploy and `project site serve` both find it:

   ```toml
   [site]
   prepare = "make docs-wasm"
   ```

   The build runs it in the action's own checkout just before building the
   site. Set up its toolchain in steps before the action, and add the
   generator's inputs to the push `paths` so a change to them redeploys:

   ```yaml
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-go@v5
           with:
             go-version-file: go.mod
         - id: site
           uses: ninjudd/projector/actions/site@v0
   ```

   The action's `prepare` input runs a different command in its place. On
   your own machine, `project site serve` runs the command only after you
   allow it once with `--allow-prepare`, so previewing a branch or a clone
   you have not read never runs its code unasked.

3. Publish something. Once the workflow is on the default branch, the
   `walkthrough-pr` skill publishes to the site by default: ask your agent
   for a walkthrough of a pull request and it pushes the spec to the hidden
   ref, starts the workflow, and hands you the link. Ask for a Claude
   Artifact instead when you want a private page. The site's Reviews section
   lists every walkthrough, and each pull request's newest version is at
   `reviews/<number>/`. The projects and the docs appear on the first
   deploy, without publishing anything.

### Serve the site without GitHub Pages

You don't need GitHub Pages to read the site. From a checkout, `project site
serve` builds the same site a deploy builds and serves it on your machine,
rebuilding it whenever `README.md`, `docs/`, or the plans change:

```sh
project site serve
```

It fetches the published walkthroughs from `origin` first, so the Reviews menu
matches what the repository has published. Publish a walkthrough without
starting a deploy with `project walkthrough publish --no-dispatch`. To host
the site somewhere other than GitHub Pages, run `project site build --out
DIR --base PATH` and serve `DIR` from any static host that answers a missing
path with `404.html`.

## Use the CLI

```sh
project init [--site | --no-site] [--action-ref <ref>] [--json]
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
project walkthrough init --repo OWNER/NAME --pr <number> --spec <file>
project walkthrough publish --spec <file> [--no-dispatch]
project site build --out <dir> [--base <path>] [--walkthroughs <dir>]
project site serve [--port 8000] [--host 127.0.0.1] [--base <path>]
project site page --spec <file> --out <dir>
project site status --repo OWNER/NAME [--pr <number>]
project site workflow [--write]
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
