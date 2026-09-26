# Install Projector skills

Projector keeps one canonical skill tree under `skills/`. The Claude Code
manifest at `.claude-plugin/plugin.json` and the Codex manifest at
`.codex-plugin/plugin.json` both point to that directory. A host loads the same
instructions and supporting scripts without a generated copy or host-specific
fork.

## Install with one command

The installer at projector.bot installs the CLI from the newest release and
the plugin for each host you have, without git:

```sh
curl -fsSL https://projector.bot/install.sh | bash
curl -fsSL https://projector.bot/install.sh | bash -s -- status
```

It takes the same targets as `./install.sh` below. Without a checkout it
installs the CLI with `pipx` from the source archive of `PROJECTOR_REF`
(default `v0`, which every release moves), or, without `pipx`, into a virtual
environment under `~/.local/share/projector` with `project` linked into
`~/.local/bin`. It adds the `PROJECTOR_REPO` marketplace (default
`ninjudd/projector`) to each host. `status` compares the installed versions
with the release's. `project upgrade` downloads and runs the same installer.
projector.bot serves `install.sh` from the `v0` tag, so it is always the
newest release's installer.

## Install for Claude Code

Add the Projector marketplace and install its plugin at user scope:

```sh
claude plugin marketplace add ninjudd/projector --scope user
claude plugin install projector@projector --scope user
```

Invoke a skill with the plugin namespace, for example:

```text
/projector:plan plan a safer deploy workflow
```

Validate a checkout before publishing it:

```sh
claude plugin validate .
claude plugin validate skills
```

## Install for Codex

Add the same repository as a Codex marketplace and install the plugin:

```sh
codex plugin marketplace add ninjudd/projector
codex plugin add projector@projector
```

Invoke a skill directly, for example:

```text
$plan plan a safer deploy workflow
```

The Codex manifest exposes the same `skills/` path as Claude Code. It adds only
install-surface metadata; it does not wrap or rewrite skill instructions.

## Reach every session through the repository

A skill's instructions load only while the skill is invoked, and neither host
loads a plugin's own `CLAUDE.md` or `AGENTS.md` into other repositories. So the
plugin is not how Projector's conventions reach a session that never invokes a
skill. `project init` is: it writes a marked section into the repository's
`AGENTS.md`, which Codex reads directly, and links it as `CLAUDE.md`, which
Claude Code reads. The text lives in the repository, so every
collaborator gets it whether or not they installed the plugin, and `project
check` warns when it drifts from the template the CLI ships. See
[the CLI guide](cli.md#adopt-a-repository).

## Install from a checkout

Run the installer to add the repository this checkout was cloned from as both
hosts' marketplace, install its plugin, and install the CLI with `pipx` from
the checkout:

```sh
./install.sh all
```

Select one component with `cli`, `claude`, or `codex`. The host-only commands
do not install the CLI, so install it separately before running a project
workflow. `all` installs each host CLI it finds, skips a missing host, and exits
69 only when neither Claude Code nor Codex is installed.

The marketplace source is the checkout's `origin` remote, so a fork installs
from the fork. A GitHub remote becomes `owner/repo` for Claude Code and the
HTTPS URL for Codex; any other remote URL is handed to both hosts as it is;
a checkout with no `origin` installs from itself. The plugin reads the
repository rather than the checkout because a host refreshes a marketplace
from its source: one that reads a checkout installs whatever commit is
checked out and then reports itself current until someone pulls, while one
that reads the repository moves with every release.

The same command upgrades what it installed. A marketplace that reads a local
path is moved to the repository, removed and added again from that source,
and a `moved` row names the old path and the new source. One that already
reads a remote is refreshed from wherever it points rather than added again.
An installed plugin is updated rather than installed again, because a host
asked to install a plugin it already has leaves it as it is; the update is
what moves it. Claude Code applies the update on its next start. A plugin
moves only when its manifest version does, because a host caches a plugin by
that version.

`pipx` installs a copy of the source rather than a link to your checkout, so
pulling new commits does not update the command. Ask which one you have:

```sh
./install.sh status
```

`cli-current` reports a command whose installed source matches the checkout.
`cli-stale` reports one that differs, or one too old to say where its source
lives, and tells you to run `./install.sh cli`. The comparison is of the files
themselves, so it holds whether or not anyone remembered to bump a version.

`marketplace` names each host's source for the `projector` marketplace, which
is where an upgrade refreshes from. `from-checkout` follows it, with a ⚠️
marker, when that source is a local path and the checkout names a repository
to move it to: the plugin goes stale with the checkout until `./install.sh
<host>` moves it. `plugin-current` reports an installed plugin at the
checkout's manifest version, `plugin-stale` one at another version, and
`plugin-absent` a host with none.

The first row is about the checkout itself. The installer fetches the current
branch's upstream and prints `repo-current` when the checkout matches it,
`repo-behind` with the commit count and a `git pull` hint when it does not,
and `repo-untracked` when a detached `HEAD` or a branch without an upstream
leaves nothing to compare against. `repo-behind` carries a ⚠️ marker and
prints in bold yellow on a terminal, because the command is built from the
checkout while a GitHub marketplace serves the plugin, so a checkout nobody
pulled installs an old command beside a current plugin and the two version
numbers look like a bug. Every install target prints the same row last. Set
`PROJECTOR_OFFLINE=1` to skip the fetch and compare against the last one; a
fetch that fails does the same and says so in the row. Set `NO_COLOR` to keep
the row plain.

`project upgrade <target>` runs this installer from any directory, for a
command installed from this checkout: `project upgrade cli` is
`./install.sh cli`, and `project upgrade status` is `./install.sh status`.

A reinstall keeps the Python the existing venv was created with. Left to
itself, `pipx` builds under its default interpreter, which on some machines is
older than Projector's 3.11 floor and fails the reinstall even though the venv
already holds a Python that works.

## Upgrade from agent-config

The old installer linked whole configuration and skill directories into each
host. Projector installs a named plugin instead. During an upgrade,
`install.sh` removes a legacy link only when its target exactly matches this
checkout's old source path. It leaves regular files, other symlinks, settings,
credentials, personal instructions, and unrelated plugins untouched.

Inspect the upgrade before applying it:

```sh
./install.sh status
./install.sh claude
./install.sh codex
```

The host plugin managers own installed copies and future updates after the
migration.

## Release an update

Projector has one version. `setup.cfg` names it for the CLI, and
`.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` name it for the
plugin, and a packaging test fails when the three disagree. One number means
one answer to "what am I running," and it lets a skill rely on the CLI that
shipped beside it.

The plugin version is what a release delivers: a host caches an installed
plugin in a directory named by that string, so an update that finds an
unchanged version resolves to the copy already on disk and installs nothing.
Claude Code records the path it used, which you can read back:

```sh
jq '.plugins["projector@projector"]' ~/.claude/plugins/installed_plugins.json
```

A release is a deliberate decision to ship what has merged, and only a
release changes the version. Other pull requests leave it alone, even when
they change skills or the CLI, so everything merged since the last tag ships
together in the next release rather than each change spending a number that
is never tagged.

Release from GitHub with the Release workflow. Start it by hand and choose
which part of the version to raise:

```sh
gh workflow run release.yml --repo ninjudd/projector -f bump=patch
```

`bump` is `patch`, `minor`, or `major`, and `patch` is the default in the
Actions tab. The workflow writes the next version to all three files on a
`release/vX.Y.Z` branch and opens a pull request for it. Merging that pull
request is the decision to ship: the same workflow runs on the merge, tags
the merged version, moves the major tag, and creates the GitHub release. The
workflow opens the pull request with its own token, which needs **Allow
GitHub Actions to create and approve pull requests** turned on under the
repository's **Settings > Actions > General**.

The workflow runs `scripts/release.py`, which also works by hand from any
checkout. `bump` raises one part of the version and `set` writes an exact
one, both refusing a version that does not go up:

```sh
scripts/release.py bump minor
scripts/release.py set 0.6.0
```

Once the bump merges, `tag` releases it. `released` exits 0 when the merged
version already has a tag and 3 when it does not, which is how the workflow
decides, on every `setup.cfg` change, whether there is anything to release:

```sh
scripts/release.py released
scripts/release.py tag
```

`tag` reads the version from `origin/main`, not from the checkout, so an
unmerged bump cannot name a release. It pushes an immutable `v0.6.0` tag,
force-moves the major tag, `v0`, to the same commit, and creates the GitHub
release for `v0.6.0` with generated notes, which needs an authenticated `gh`.
Pass `--no-release` to push only the tags. If the release step fails, the tags
are already pushed, and the error prints the `gh release create` command that
finishes the release. The immutable tag names an exact release; the major tag
is the channel that follows compatible releases, the convention GitHub's own
actions use. A breaking change starts the next major tag. Under `0.x` a minor
release may still break, so the `v0` channel promises less than a `v1` channel
will.

Update an installed copy with each host's own command, or run `./install.sh
all` from a checkout, which runs these for every host it finds. Claude Code
needs a restart to apply the update, and Codex refreshes a Git marketplace
snapshot before it can see the new version:

```sh
claude plugin update projector@projector
codex plugin marketplace upgrade
codex plugin add projector@projector
```

## Work without MCP

Projector currently ships no MCP server. Every core skill uses the public
`project` CLI and ordinary Git or GitHub commands. A future MCP adapter can
expose the same versioned project operations without becoming the package that
contains the skills.
