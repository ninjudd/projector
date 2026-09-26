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

The Projector site's scripts are TypeScript under `src/projector/site/ts/`.
After you edit one, check it, compile it, and commit the JavaScript it
produces under `src/projector/site/assets/`, which is what every deploy and
install serves:

```sh
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

Run the validation gate from the repository root. Run `npm ci` once first, or
the test that compares the compiled scripts with their TypeScript skips:

```sh
PYTHONPATH=src python3 -m unittest discover -v
PYTHONPATH=src python3 -m projector check
claude plugin validate .
claude plugin validate skills
git diff --check
```

The Check workflow runs `npm run check`, the tests, and `project check` on
every pull request.

## Release

A release is a deliberate step, and only a release changes the version. Run
the Release workflow from the Actions page, or with
`gh workflow run release.yml -f bump=patch`, and merge the pull request it
opens. Merging tags the release and moves `v0`, which is what the installer,
`project upgrade`, and every repository's site workflow use. See
[Release an update](plugins.md#release-an-update).
