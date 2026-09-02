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

## Install from a checkout

Run the installer to add the local checkout as both marketplaces and install
the CLI with `pipx`:

```sh
./install.sh all
```

Select one component with `cli`, `claude`, or `codex`. The host-only commands
do not install the CLI, so install it separately before running a project
workflow. `all` installs each host CLI it finds, skips a missing host, and exits
69 only when neither Claude Code nor Codex is installed.

`pipx` installs a copy of the source rather than a link to your checkout, so
pulling new commits does not update the command. Ask which one you have:

```sh
./install.sh status
```

`cli-current` reports a command whose installed source matches the checkout.
`cli-stale` reports one that differs, or one too old to say where its source
lives, and tells you to run `./install.sh cli`. The comparison is of the files
themselves, so it holds whether or not anyone remembered to bump a version.

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

Update an installed copy with each host's own command. Claude Code needs a
restart to apply the update, and Codex refreshes a Git marketplace snapshot
before it can see the new version:

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
