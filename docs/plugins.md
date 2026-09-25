# Install Projector skills

Projector keeps one canonical skill tree under `skills/`. The Claude Code
manifest at `.claude-plugin/plugin.json` and the Codex manifest at
`.codex-plugin/plugin.json` both point to that directory. A host loads the same
instructions and supporting scripts without a generated copy or host-specific
fork.

## Install for Claude Code

Add the Projector marketplace and install its plugin at user scope:

```sh
claude plugin marketplace add ninjudd/projector --scope user
claude plugin install projector@projector --scope user
```

Invoke a skill with the plugin namespace, for example:

```text
/projector:plan-project plan a safer deploy workflow
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
$plan-project plan a safer deploy workflow
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

Projector ships two artifacts and they carry separate versions. Bump the
**plugin** version in both `.claude-plugin/plugin.json` and
`.codex-plugin/plugin.json` when skills, scripts, or manifests change; a test
asserts those two agree, because they describe one plugin to two hosts and a
one-sided bump leaves the other on a stale cache entry. Bump the **CLI**
version in `setup.cfg` when the CLI changes. The two need not match.

Nothing depends on the CLI version being punctual. `./install.sh status`
compares installed files against the checkout rather than version strings, so
a release that forgets `setup.cfg` costs an inaccurate number and not a
command that reports itself current while being behind.

The plugin version is what a release delivers: a host caches an installed
plugin in a directory named by that string, so an update that finds an
unchanged version resolves to the copy already on disk and installs nothing.
Claude Code records the path it used, which you can read back:

```sh
jq '.plugins["projector@projector"]' ~/.claude/plugins/installed_plugins.json
```

The packaging tests assert both manifests declare the same version, because a
one-sided bump updates one host and leaves the other on its stale cache entry.

Tagging follows the bump rather than replacing it. `claude plugin tag` derives
the tag name from the manifest, so it can only publish a version the manifest
already declares, and it refuses a dirty working tree:

```sh
claude plugin tag --dry-run .
claude plugin tag . --push
```

The tag is `projector--v<version>`, and the command validates that
`plugin.json` and the enclosing marketplace entry agree before creating it.

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
