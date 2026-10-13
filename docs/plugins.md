# Install Projector

Projector is two things to install: the `project` command, and the plugin
that gives Claude Code and Codex its skills. The framework is Projector; the
command it installs is `project`. The repository, the `projector-cli`
distribution, the Python package, and the `projector@projector` host plugin
all keep the longer name.

Projector keeps one canonical skill tree under `skills/`. The Claude Code
manifest at `.claude-plugin/plugin.json` and the Codex manifest at
`.codex-plugin/plugin.json` both point to that directory. A host loads the same
instructions and supporting scripts without a generated copy or host-specific
fork.

The plugin provides `spec`, `implement`, `finish`, `review-pr`, `fix-pr`,
`start-review-loop`, `start-watch-loop`, `summarize-pr`, and `write`.
`summarize-pr` summarizes a pull request's changes as a guided review page.
Claude invokes a plugin skill as `/projector:<skill>`; Codex invokes it as
`$<skill>`.
`spec` writes a project's technical spec under `docs/projects/`.
`implement` builds from that plan, or from a description of a change small
enough to need no spec, such as `/projector:implement fix the typo in the
install docs`.
`review-pr` reviews a pull request's current head once, by the method in
`skills/review-pr/method.md`, and publishes one labeled review;
`start-review-loop` runs it on every new head of your pull requests and of
those that request your review. `fix-pr` answers one pull request's
findings, verifying each by the same protocol, and merges the base into a
branch that conflicts with it; `start-watch-loop` runs it, one subagent per
pull request, whenever its watcher reports work on one this conversation
owns. `implement` starts that loop on every pull request it opens, or adds
the pull request to the loop the conversation already runs,
and a loop it starts ends once every pull request it watches has merged or
closed. Only the conversation you talk to runs the loop. An `implement`
running as a subagent hands its pull request up, through every subagent
above it, to that conversation, which starts or joins the loop on it, and a
subagent asked to start the loop hands that request up the same way. Where
the host cannot keep the loop running, `implement` says so when it hands the
pull request over. Set `watch.auto_start = false` in `.projector.toml` to
start the loop yourself instead. `fix-pr` also implements or defers each
suggestion a review lists. While it builds a change that no review has read,
such as a
suggestion it implements or a change you ask for, it holds the pull request
in draft with a comment that lists the changes, so the pull request never
looks ready to merge while the changes are coming. It posts and removes that
comment with `project review hold`, and ends the hold once the changes are
pushed, so a clean review of that push marks the pull request ready. The
reviewer settles its own
findings on every head: it resolves one it verifies fixed, reopens one resolved
too early, and keeps one whose decline does not hold, so its verdict rests on
what it checked rather than on who clicked resolve. In a repository with a
Projector site, `review-pr` then runs `summarize-pr` on every pull request it
reviews and links the summary from its review; `review.summarize = false` in
`.projector.toml` turns that off. All three of `implement`,
`review-pr`, and `fix-pr` share the code guidelines in
`skills/guidelines.md`, so the rules one follows are the rules the others
review and fix against. Every skill that writes prose a person reads, such as
a plan, a review, a summary, or a pull request body, follows the writing
guide in `skills/writing.md`. `write` applies the same guide to any other
prose you ask for, such as `/projector:write tighten the install section of
the README`. The core workflows use the local CLI and do not require MCP.

When dependent work needs more than one pull request to stay reviewable,
`implement` opens it as a stack that GitHub records, using the `gh stack`
GitHub CLI extension, and `fix-pr` carries a fix on a lower layer up through
the layers above it right away. Every layer stays on the current head of the
layer below it, so you can merge the whole stack, or any number of layers
from the bottom up, in one step from GitHub, and `start-watch-loop` reports
which of them are ready to merge together. The extension is optional.
Without it, or in a repository that does not have stacks enabled,
`implement` chains the pull requests by base branch and tells you once, and
`fix-pr` and `start-watch-loop` find a stack's layers from those base
branches. Install it with `gh extension install github/gh-stack` to get
real stacks.

GitHub's own `gh-stack` skill teaches an agent the extension's commands.
Projector does not copy it. Both hosts' Projector marketplaces serve a
`gh-stack` plugin that holds upstream's skill from
[`github/gh-stack`](https://github.com/github/gh-stack) unmodified. In
Claude Code, the `projector` plugin depends on it, so installing `projector`
installs it too, and you invoke it as `/gh-stack:gh-stack`. Codex has no
plugin dependencies, so there you add it as a plugin of its own, as
[Install for Codex](#install-for-codex) shows; Codex lists it as
`gh-stack:gh-stack`. Where upstream's skill and Projector's workflow
differ, `implement` says which rules win, such as drafting every layer and
never merging. Projector's skills work without upstream's skill, because
they name the `gh stack` commands they run.

Both marketplace entries, in `.claude-plugin/marketplace.json` and
`.agents/plugins/marketplace.json`, pin upstream to one tag, its commit, and
the matching `version`, so a newer upstream skill reaches you only when a
Projector release moves all three in both files. A packaging test fails when
the two entries disagree.

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
with the release's. `project upgrade` downloads and runs the same installer,
and every `project` command warns on stderr when a newer release is out (see
[the CLI guide](cli.md#know-when-a-release-is-out)). projector.bot serves
`install.sh` from the `v0` tag, so it is always the newest release's
installer. Set `PROJECTOR_REF` to install a particular release, such as
`v0.5.7`, and `PROJECTOR_REPO` to install from a fork.

The installer also installs or moves the `gh-stack` plugin on each host,
since `claude plugin update` neither installs a dependency a plugin newly
declares nor moves one already installed. A marketplace that does not serve
`gh-stack` yet leaves Projector installed and prints a `gh-stack` warning.
The `all` target, the default, also installs the `gh stack` extension with
`gh extension install github/gh-stack` when `gh` is installed and the
extension is not, and runs `gh extension upgrade stack` when the installed
extension is older than the release the marketplace pins. It leaves an
extension that provides `gh stack` from another owner alone, prints a
`skipped` row when `gh` is missing, and never fails the install over the
extension. `status` adds a `stack-` row comparing the extension with the
pin.

## Install only the CLI

`pipx` installs an isolated `project` from the newest release's source
archive. It needs Python 3.11 or newer:

```sh
pipx install https://github.com/ninjudd/projector/archive/v0.tar.gz
project --help
```

## Install for Claude Code

Add the Projector marketplace and install its plugin at user scope:

```sh
claude plugin marketplace add ninjudd/projector --scope user
claude plugin install projector@projector --scope user
```

The install also installs the `gh-stack` plugin that `projector` depends on.
Invoke a skill with the plugin namespace, for example:

```text
/projector:spec a safer deploy workflow
```

To update by hand, refresh the marketplace and update both plugins.
`claude plugin update projector@projector` moves only Projector, and
Projector fails to load while `gh-stack` is missing, so install `gh-stack`
when you do not have it yet:

```sh
claude plugin marketplace update projector
claude plugin update projector@projector
claude plugin install gh-stack@projector --scope user   # when it is missing
claude plugin update gh-stack@projector
```

Validate a checkout before publishing it:

```sh
claude plugin validate .
claude plugin validate skills
claude plugin test .
```

### See your loops' pull requests in Projector's band

In Claude Code, the plugin also ships a hooks module in `hooks/` that lists
the open pull requests your loops track in the band above the prompt, one
line each. Run `/projector` to show or hide the band. It also appears when the
session starts a loop's watcher for the first time.

```text
───────────────────────────────────────────────── updated 12s ago [-]

#267 draft List a loop's open pull requests above the Claude Code prompt
#258 draft Name the reviewed head in a review's signature line  conflict
           reviewing · by ada · review-signature · +84 −12 in 5 files · checks passing
           discuss  github  projector
```

Each line is the pull request's number, `draft` or `ready`, the title, and,
when the pull request has them, a merge conflict, a change request, or
unresolved review threads. A pull request that both a `start-watch-loop`
watcher, `watch-threads.sh`, and a `start-review-loop` watcher,
`watch-prs.sh`, track is listed once. Only the title shortens on a narrow
terminal. The divider says how long ago the band reloaded, and Claude Code's
`[-]` at its end collapses the band. A blank line sets the divider apart from
the rows under it. Only a terminal draws the divider and that blank line.
Claude Desktop, VS Code, and the mobile app do not set the band's text in a
terminal's columns, so a rule sized to them would wrap, and there the band
starts with its first row.

Click a pull request's number to open a drawer under it, and click it again
to close the drawer; opening one drawer closes any other. The drawer says
whether your loops are watching or reviewing the pull request, then its
author, branch, size, checks, and when it last changed. Below that:

- **discuss** puts the pull request's number in the prompt at the cursor,
  as `#258`, or as `owner/repo#258` when the band lists more than one
  repository.
- **github** opens the pull request on GitHub.
- **projector** opens its Projector summary, when the pull request's
  description links one.

On the main screen, which Claude Code uses under tmux and when
`CLAUDE_CODE_NO_FLICKER=0` is set, press ctrl+x tab to move into the band,
then Enter on a number to open its drawer. The drawer's links take no press
there; the terminal opens them the way it opens any other link.

The band lists only this session's loops. It finds a loop by the
`--tracked` file its watcher reads, and learns that file when the session
runs the watcher. A loop another session runs stays out of this session's
band. To show a tracked file by hand, such as another session's, name it:

```text
/projector ~/loops/review/tracked
```

The band reloads when one of the session's watchers reports a change and
when a turn ends, and every 30 seconds otherwise. Each reload asks GitHub
for every pull request in one `gh api graphql` call, and only while the band
is shown. The skills never depend on the band, and Codex has no equivalent.

### Read your inbox in Projector's band

An agent in Claude Code asks you a question with Projector's `ask` tool,
which the model sees as `mcp__projector__ask`, and tells you something that
needs no answer with the `tell` tool, `mcp__projector__tell`. Either way it
keeps working. An agent that runs `project inbox ask` or `project inbox tell`
from the shell reaches the same inbox, as
[Use the inbox](cli.md#use-the-inbox) describes.

The items are a list below the pull requests, oldest first. A question's
line starts with `-`, and a notice's with `•`, so you can tell which items
want an answer and which want a read. Each line ends with the pull request
the item is about, as the band labels it, and how long ago it was posted.
The band appears when an item arrives, even after you hid it, and the
divider counts the open questions and the open notices.

```text
──────────────────────────────── 2 questions · 1 notice · updated 12s ago [-]

#337 draft Retry failed webhook deliveries with backoff

- Run project init to add the publish rule?                             2m
- Decline the finding about the retry budget?                     #337  5m
• Force-pushed #337 to drop a stray debug file                    #337  1m
```

Click anywhere on an item to open its drawer, and click it again to close
it. On the main screen, press ctrl+x tab to move into the band, then Enter on
an item. While its drawer is open, the item stays highlighted and has the
band to itself, the divider included, so the item is the top row and the
drawer has one more row before the band scrolls:

```text
- Decline the finding about the retry budget?                 #337  5m [-]

  The review asks for a retry budget per host. The loop caps retries at
  three in total, which covers the case the finding names.

  1. Decline it: the total cap already bounds retries to one host
     (recommended)
  2. Add a budget of two retries per host, and keep the total cap
  3. [ Type something                                                    ]

  discuss | dismiss | snooze
```

The first option is always the agent's recommendation. Click an option, or
type an answer and press Enter, and the band puts the question and that
answer in your prompt, for you to send as it stands or change first:

```text
"Decline the finding about the retry budget?"

Add a budget of two retries per host, and keep the total cap
```

Sending that prompt answers the question. The band finds each quoted question
whose quote ends its line, and takes the next line that is not blank as its
answer: the option with that text, or else an answer you typed. Each quote
answers one question: when two open questions ask the same thing, it answers
the one whose drawer last put that same answer in your prompt, or else the
older one. Only a prompt you send, at the terminal or through Remote
Control, answers a question. Another session's message or a notification
that quotes one answers nothing. Your prompt is what carries the answer to
the main loop, and when a subagent asked, the main loop also reads a note
naming it, so it can pass the answer on.
**discuss** puts only the quoted question in the prompt, followed by a space,
so what you type after it on the same line talks the question over and
answers nothing. **dismiss** sets the question aside without an answer, and
**snooze** hides it for an hour.

A notice's drawer shows its detail, then the same `discuss | dismiss |
snooze`. **discuss** puts the quoted notice in the prompt, and a prompt that
quotes a notice answers nothing. **dismiss** clears the notice once you have
read it, and nothing goes back to the agent.

A snoozed item leaves the band and the divider's counts at once. When its
hour passes, it returns to the list, and the band appears if you hid it. To
pick another time, or to wake an item now, run
`project inbox snooze <reference> --for <minutes>` in a terminal. An item you
answer or dismiss from a terminal while it is snoozed does not return.

A resolved item stays in the list, dim, for a minute after it is delivered,
and a dismissed one is struck through. A dismissed notice is delivered at
once, since nothing goes back to the agent. A question's drawer still opens
and marks the answer given with `✓`, and a dismissed notice's drawer keeps
only **discuss**.

An answer you give from a terminal with `project inbox answer` reaches the
agent through the band of the session that asked. A subagent that is still
running receives it directly, as a message that starts `ANSWER`, or
`DISMISSED` for a dismissal. Otherwise the main loop receives it as a prompt
of its own once its turn ends, with the name of the subagent that asked, and
`sending` follows the question until it has gone. A reference to a question
in a prompt, such as `#337.3.b`, reaches the agent with the question it
names.

Shown or hidden, the band lists the session's inbox directory every 5
seconds and runs `project inbox` only when the directory changed since
its last successful read or an answer is on its way, so a session with an
empty inbox starts no process. A read that fails runs again 5 seconds later.

## Install for Codex

Add the same repository as a Codex marketplace and install the plugin:

```sh
codex plugin marketplace add ninjudd/projector
codex plugin add projector@projector
codex plugin add gh-stack@projector
```

Invoke a skill directly, for example:

```text
$spec a safer deploy workflow
```

The Codex manifest exposes the same `skills/` path as Claude Code. It adds only
install-surface metadata; it does not wrap or rewrite skill instructions.

Codex has no plugin dependencies, so `gh-stack@projector` is a plugin of its
own there, and the last command above installs it. Projector's skills work
without it. To update both, run `codex plugin marketplace upgrade projector`
and then both `codex plugin add` commands again, since `add` installs a
plugin again from the marketplace when it is already there.

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

Projector has one version. `pyproject.toml` names it for the CLI, and
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

GitHub starts no workflow for a pull request that a workflow's own token
opened, so the Check workflow would never run on the release pull request by
itself. The Release workflow dispatches Check on the release branch instead,
both when it opens the pull request and when a rerun finds one already open,
because `workflow_dispatch` is the one event that token may trigger. The
dispatched run's checks land on the branch's commit, which is the pull
request's head, so they show on the release pull request like any other
checks. That is why `.github/workflows/check.yml` lists `workflow_dispatch`
among its triggers and the Release workflow's propose job has `actions: write`.

The workflow runs `.github/scripts/release.py`, which also works by hand
from any checkout. `bump` raises one part of the version and `set` writes an
exact one, both refusing a version that does not go up:

```sh
.github/scripts/release.py bump minor
.github/scripts/release.py set 0.6.0
```

Once the bump merges, `tag` releases it. `released` exits 0 when the merged
version already has a tag and 3 when it does not, which is how the workflow
decides, on every `pyproject.toml` change, whether there is anything to
release:

```sh
.github/scripts/release.py released
.github/scripts/release.py tag
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
claude plugin update gh-stack@projector
codex plugin marketplace upgrade
codex plugin add projector@projector
codex plugin add gh-stack@projector
```

A release that first ships the `gh-stack` dependency leaves Projector unable
to load in Claude Code for anyone who updates only `projector@projector`,
because `update` does not install the new dependency. Run
`claude plugin install gh-stack@projector --scope user` once, or
`project upgrade`, which does it for you.

Taking a newer upstream `gh-stack` is a deliberate change of its own: set
the same `ref`, `sha`, and `version` on the `gh-stack` entry in both
marketplace files, and read upstream's new skill against the rules in
`implement`'s "Stack dependent work" section.

## Work without MCP

Projector currently ships no MCP server. Every core skill uses the public
`project` CLI and ordinary Git or GitHub commands. A future MCP adapter can
expose the same versioned project operations without becoming the package that
contains the skills.
