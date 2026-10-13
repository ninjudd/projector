# Develop Projector

Read [AGENTS.md](../AGENTS.md) before you change anything: it holds the project
model, the portability rules, the validation gate, and how work is handed over
for review. It is the same file as `CLAUDE.md`.

## Run the CLI from a clone

The CLI has no runtime dependencies, so a clone runs it without installing
anything:

```sh
git clone https://github.com/ninjudd/projector.git
cd projector
PYTHONPATH=src python3 -m projector --help
```

For a `project` command that follows your edits, install the clone in editable
mode in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/project --help
```

Keep the `project` on your `PATH` the released one, from
`curl -fsSL https://projector.bot/install.sh | bash`, so the command you work
with day to day is the one every user gets. `project upgrade` moves it to the
newest release.

## Try unreleased skills

A host loads the plugin from a directory for one session, without touching
the installed plugin:

```sh
claude --plugin-dir /path/to/projector
```

Codex reads a local marketplace live, so `codex plugin marketplace add
/path/to/projector` followed by `codex plugin add projector@projector` serves
the clone's skills until you add the GitHub marketplace back with
`curl -fsSL https://projector.bot/install.sh | bash`.

`./install.sh all` installs a clone's CLI with `pipx` and adds the repository
the clone came from as each host's marketplace. `pipx` installs a copy, so
pulling new commits updates neither the command nor the plugin until you run
it again, and it ends by saying whether the clone is behind its upstream.
`./install.sh status` shows what is installed from where. The installer
removes only legacy symlinks that point from a host's old agent-config
locations into the clone, and never replaces a configuration directory or a
user-owned file.

## Build the site's scripts

The Projector site's front end lives in `site/`, apart from the Python
package: TypeScript under `site/src/`, and the stylesheets, icon, and compiled
JavaScript under `site/assets/`, which is what every deploy and install
serves. After you edit a script, check it, compile it, and commit the
JavaScript it produces:

```sh
cd site
npm ci
npm run check   # type-check and lint, with no warnings allowed
npm run build
```

Resolve a conflict in a compiled file by taking either side and running
`npm run build`. To see the site a repository gets, build and serve it from a
clone with `PYTHONPATH=src python3 -m projector site serve`.

## Work on projector.bot

The website at <https://projector.bot> is the Next.js app in `web/`, which
Vercel deploys from `main`. See [web/README.md](../web/README.md):

```sh
cd web
npm install
npm run dev
```

## Validate a change

Run the validation gate from the repository root. Prepare each checkout once
first:

1. Run `npm ci` in `site/`. Without it, the test that compares the compiled
   scripts with their TypeScript skips, and `hooks/typecheck.sh` has no
   compiler.
2. Start an interactive session with `claude --plugin-dir .` from the
   repository root. When the session loads the checkout as a plugin, Claude
   Code writes the declarations the hooks module compiles against to
   `.claude-plugin/types/`, which Git ignores. They stay after you quit the
   session. Projector loads only when its `gh-stack` dependency is installed,
   as [Install for Claude Code](plugins.md#install-for-claude-code)
   describes. Start such a session again after you update Claude Code. A
   linked worktree can skip this step when the main checkout has the
   declarations.

```sh
PYTHONPATH=src python3 -m unittest discover -v
PYTHONPATH=src python3 -m projector check
claude plugin validate .
claude plugin validate skills
claude plugin test .
hooks/typecheck.sh
git diff --check
```

`hooks/typecheck.sh` runs `site/`'s `tsc` over `hooks/` with the root
`tsconfig.json`, which extends the one Claude Code writes beside its
declarations. The declarations depend mainly on the Claude Code version, so in
a linked worktree that has none, the script uses the main checkout's, the
first one `git worktree list` prints, and names it. It writes nothing to
either checkout. When neither has the declarations, the script fails and says
how to get them.

The Check workflow runs `npm run check`, the tests, and `project check` on
every pull request. It cannot type-check `hooks/`, because only an
interactive Claude Code session writes the declarations.

## Release

A release is a deliberate step, and only a release changes the version. Run
the Release workflow from the Actions page, or with
`gh workflow run release.yml -f bump=patch`, and merge the pull request it
opens. The workflow dispatches the Check workflow on that pull request, because
GitHub runs no workflow for a pull request that a workflow's own token opened.
Merging tags the release and moves `v0`, which is what the installer,
`project upgrade`, and every repository's site workflow use. See
[Release an update](plugins.md#release-an-update).
