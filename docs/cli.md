# Use the Projector CLI

Projector installs one command, `project`. It discovers the Git root from your
current directory and reads projects under `docs/projects/`. Pass
`--root <path>` to select a repository explicitly, or `--projects-dir <path>` to
use another project-plan directory.

Running the package as a module is equivalent when the command is not on your
`PATH`:

```sh
python3 -m projector check
```

Print the version of the installed command:

```sh
project --version
```

## Adopt a repository

Run `init` from anywhere inside a Git repository:

```console
$ project init
created docs/projects/README.md
created AGENTS.md
created CLAUDE.md
created .claude/settings.json
```

`init` manages four files and reports what it did to each:

- `docs/projects/README.md` states the convention, links to the Projector
  repository, and shows how to install the CLI, so a reader who meets
  `docs/projects/` in another repository knows where the `project` command
  comes from. `init` creates it once and never rewrites it.
- `AGENTS.md` carries a section Projector generates between two HTML-comment
  markers: where plans live, how to use `project`, and the writing style for
  documentation and GitHub prose. `init` appends the section to an existing
  file or creates the file with only that section, and refreshes the section
  when it falls behind the template this command ships. Everything outside the
  markers is yours and is never changed.
- `CLAUDE.md` is a symlink to `AGENTS.md`, because Claude Code reads
  `CLAUDE.md` and Codex reads `AGENTS.md`, and one file can serve both. `init`
  creates the link when `CLAUDE.md` is absent, or a file containing the import
  `@AGENTS.md` where the platform cannot make a symlink. A repository with
  only a `CLAUDE.md` gets `AGENTS.md` as a link to it instead, or, where no
  link can be made, a regular `AGENTS.md` with the section while `CLAUDE.md`
  gains the section too, since Codex has no import syntax. A `CLAUDE.md`
  that already links to or imports `AGENTS.md`, on its own line or inline in a
  sentence, is left alone; a mention inside backticks or a code block is not
  an import. When the two are genuinely distinct files, each with its own
  content and no import between them, each gets the section, because an import
  would change everything Claude Code reads rather than only Projector's part.
- `.claude/settings.json` gains the Claude Code permission rule
  `Bash(project review publish *)` in `permissions.allow`. Claude Code's auto
  mode otherwise refuses a review loop's clean review of your own pull request
  as self-approval, although the review is a `COMMENT` that moves no
  `reviewDecision` and a person still merges. An allow rule resolves the
  command before the classifier runs, and Claude Code applies a repository's
  allow rules only after its workspace trust dialog has listed them to you.
  Because the rule exempts a command from the classifier, `init` adds it only
  when you run `init` at a terminal, or when you pass `--publish-rule`. Run
  without a terminal, as an agent refreshing the instructions runs it, `init`
  reports the file as `kept` and says on stderr how to add the rule, and
  `review.publish_rule = true` does not change that. Pass `--no-publish-rule`
  to leave the file alone for one run. `init` adds the rule to the file's
  existing settings, keeps every other setting, and writes the file back as
  two-space JSON. It keeps a file that is not valid UTF-8 JSON, holds
  settings of an unexpected shape, sits under a `.claude` that is not a
  directory, or links outside the repository, and says on stderr how to add
  the rule yourself. Auto mode reads no `autoMode` block from a repository,
  so this rule, not an `autoMode` exception, is what a repository can carry.

Git checks a committed symlink out as a small plain file holding the link text
wherever `core.symlinks` is false, which is Git for Windows' default without
Developer Mode. On such a checkout the host reads only the word `AGENTS.md`,
so `check` reports the file as `instructions-unlinked` and `init` leaves it
alone rather than appending a section that a commit would record as the link's
target. Git can create a symlink on Windows only with the privilege that
Developer Mode grants and administrators hold, so enable Developer Mode or run
as an administrator, set `core.symlinks=true`, and check the repository out
again.

Run `init` again whenever `check` says the section is outdated. Each file is
reported as `created`, `updated`, `unchanged`, or `kept`. `kept` means `init`
left a file alone on purpose and says why on stderr, for one of four reasons:
the section is newer than this command's template, so upgrade the command
instead; the path is a symlink to a file outside the repository, which `init`
never writes; the file is a symlink checked out as a plain file, as above; or
the markers in the file do not delimit exactly one section, for example a
begin marker with no end marker. In that last case `init` still writes the
other instruction files, then exits 65 and names the repair, and prints no
JSON document in `--json` mode. `.claude/settings.json` is kept for the
reasons its entry above names, and `init` still exits 0.

To keep Projector out of your instruction files, set in `.projector.toml`:

```toml
[instructions]
enabled = false
```

With that key false, `init` manages only the projects README and
`.claude/settings.json`, and `check` says nothing about `AGENTS.md` or
`CLAUDE.md`. To leave `.claude/settings.json` alone, set:

```toml
[review]
publish_rule = false
```

Every command that reads or writes a project requires that directory. `init`
creates it, `check` reports its absence, and `config` and `upgrade` do not
look for it. When it is missing, Projector names the path it looked for and
exits 66 instead of reporting an empty repository:

```console
$ project list
project: projects directory not found: docs/projects (run 'project init' to adopt the convention)
```

`check` is the exception, because reporting problems is its job: it records the
absent directory as a `missing-projects-dir` issue and exits 65 like any other
validation failure.

An adopted repository with no projects yet is not an error. `list` prints
nothing and exits 0.

### Set up the site

`init` also sets up the [Projector site](#build-the-projector-site) for the
GitHub repository that `origin` names, using `gh`:

```console
$ project init
unchanged docs/projects/README.md
unchanged AGENTS.md
unchanged CLAUDE.md
created .github/workflows/projector-site.yml
created GitHub Pages site https://owner.github.io/example/
updated repository website https://owner.github.io/example
```

After the files above, `init` turns on GitHub Pages with GitHub Actions as
its source and reports the site as `created`, `updated`, or `unchanged`. A
repository that already deploys its own Pages site, from a branch or from
another workflow that uses `actions/deploy-pages`, keeps it: `init` skips the
site with a note. Pass `--site` to switch a branch site to GitHub Actions and
add Projector's workflow beside another deployer. When the repository is
private and its site is public, it makes the site private and prints
`updated GitHub Pages visibility: private`. It points the repository's
website link at the site when that link is empty, without the trailing slash
GitHub reports for the site, and keeps a link to anywhere else, saying so on
stderr. Last, it writes the same workflow file
as `site workflow --write` and reports it like any other file, reminding you
on stderr to merge it to the default branch through a pull request. It
updates a workflow Projector wrote, in any earlier shape, and keeps one edited
by hand, reported as `kept` with a note saying how to replace it.
`--action-ref` pins the Projector tag or commit the workflow runs, `v0` by
default.

Changing Pages or the website link takes admin rights on the repository.
Without them, `init` changes nothing on GitHub and says what an admin needs
to do; it still writes the workflow when an admin has already set Pages up,
because proposing the workflow needs no admin.

The workflow is written only once the site is safe to deploy to. When `gh`
is missing, you are not an admin and Pages needs changing, the repository
already deploys its own site, or GitHub refuses to make a private
repository's site private, as it does without private Pages (GitHub
Enterprise Cloud), `init` adopts the repository as usual, skips the site
with a note on stderr, and exits 0. If `init` created the Pages site in
that same run and cannot make it private, it deletes the site again, so a
private repository is never left with a public site; it never deletes a
site it did not create. A repository whose `origin` is not on GitHub skips
the site without a note. `project site serve` still serves the site
locally. Pass `--site` to make the other failures an error that exits 65,
or `--no-site` to leave GitHub alone. To skip the site every time, set in `.projector.toml`:

```toml
[site]
enabled = false
```

In `--json` mode the document gains a `site` object with `pages`,
`visibility`, `url`, `public`, and `website`, or with `skipped` and the
reason when the site was not set up.

## Browse projects

Run `list` to group projects by priority without creating an index. Each row
shows the project's status, and completed projects group together at the end:

```console
$ project list
now:
  projector                    in-progress  Turn agent-config into Projector
```

Add `--status ready` or `--priority next` to select one value, or both to
intersect them. Add `--owner <login>` to select the projects one person owns,
ignoring case. A row ends with its owner in brackets when the plan names one. Run `show <project>` to print the entry point, including its
frontmatter, or `search <query>` to search project names, metadata, plans, and
supplemental Markdown files.

`list` and `search` read every project, so one plan that does not match the
format fails the command rather than dropping that project from the results:

```console
$ project list
project: docs/projects/payments/readme.md: status must be one of draft|ready|in-progress|completed (run 'project check' for the full report)
```

Run `check` for every problem at once. `show <project>` still reads a single
valid plan while another plan is malformed.

Project names are paths relative to `docs/projects/`. For example,
`docs/projects/payments/invoices/readme.md` is `payments/invoices`.

## Create and update projects

Create a top-level or nested project:

```sh
project create payments --status ready --priority next
project create invoices --parent payments --priority later
```

`create` defaults to `--status draft` and `--priority later`.

`create` opens the new plan when stdin and stdout are interactive. Pass
`--no-edit` to leave the generated plan ready for another command. In a
non-interactive session, Projector never opens an editor.

Run `edit <project>` to open an existing plan with `$VISUAL` or `$EDITOR`.
`edit` has no `--json` mode because the editor owns the interactive session.
It exits 69 without a terminal or configured editor.

Change only the status scalar with `status`, only the priority scalar with
`priority`, or use `done` as a readable shorthand for
`status <project> completed`:

```sh
project status payments in-progress
project priority payments now
project done payments/invoices
```

Set or remove the optional owner with `owner`. Name one person, preferably by
GitHub login:

```sh
project owner payments octocat
project owner payments --clear
```

`owner` adds the field just before the closing `---` when the plan has none,
and `--clear` removes the line. It refuses a value that would not read back
unchanged, such as one with a leading `@`, a `: `, or a ` #`, with exit code 2.

`priority` adds the field when a plan has none, which happens only for a
completed project being rescheduled. Set the priority first in that case:
`status` refuses to move a completed plan to another status while it has no
priority, rather than writing a plan that `check` would then reject.

Projector preserves unrelated frontmatter, formatting, and uncommitted plan
content. It writes through a temporary file in the project directory and
refuses the update if the file changes after Projector reads it. Mutation
commands make no Git commits.

## Validate projects

Run `check` before handing over project changes:

```console
$ project check
Project plans are valid.
```

The command reports malformed frontmatter, invalid statuses, invalid or
missing priorities, an empty or multi-line owner, missing top-level
plans, uppercase project entry points, case collisions, symlinks, malformed
Markdown links, and missing local link targets. It reads both directory entries
and Git's tracked paths so casing errors remain visible on case-insensitive
filesystems. Those are errors: any one of them exits 65.

`check` also reports the state of the Projector section in `AGENTS.md` and in
`CLAUDE.md`. Those are warnings: they print to stderr with a
`warning:` prefix and name the fix, and they never change the exit code, so a
collaborator on an older CLI is told what to do without a failing gate.

```console
$ project check
warning: AGENTS.md: Projector section is version 1; this command ships version 2 (run 'project init' to refresh it; your own content is not changed) [instructions-outdated]
Project plans are valid.
```

Each code names the file it is about. `CLAUDE.md` is checked only when it is
a distinct file that neither links to nor imports `AGENTS.md`; a link or an
import reads the section through `AGENTS.md` and needs nothing of its own.

| Code | Condition | Fix |
| --- | --- | --- |
| `instructions-missing` | the file is absent or has no Projector section | `project init` |
| `instructions-outdated` | the section is older than this command's template | `project init` |
| `instructions-ahead` | the section is newer than this command's template | `project upgrade`, or reinstall the CLI |
| `instructions-edited` | the section was edited by hand | `project init` to restore it, or change the template in Projector |
| `instructions-malformed` | a begin marker without an end marker, or a second pair | repair the markers by hand |
| `instructions-external` | the file is a symlink to a file outside the repository | replace the link with a file in the repository, or set `instructions.enabled = false` |
| `instructions-unlinked` | the file is a symlink checked out as a plain file holding the link text | enable Developer Mode or run as an administrator, set `core.symlinks=true`, and check the repository out again |

A path that resolves outside the repository reports only
`instructions-external`; `init` never writes there, so it is the one warning
`init` cannot clear.

## Read configuration

Projector reads settings from `.projector.toml` files. Put a value in the file
nearest the code it applies to: a repository's own file is checked in with the
repository, a file in a parent directory covers every repository beneath it,
and `~/.projector.toml` covers everything you do.

Files are merged lowest precedence first:

1. `~/.projector.toml`, read wherever the repository lives.
2. Every `.projector.toml` from your home directory down to the repository
   root, nearest last.

The walk stops at your home directory, so a file above it is never read. When
the repository is not under your home directory -- a checkout at `/opt/src`, a
mounted volume, a container's `/workspace` -- no ancestor is read, and
`~/.projector.toml` is still applied. A worktree inside the repository, as
`.claude/worktrees/<name>` is, keeps the repository and its parents on the
walk.

Tables merge key by key, so a nearer file overrides one setting without
discarding the rest:

```toml
# ~/ninjudd/.projector.toml — every repository in this directory
[review]
username = "minjudd"
summarize_min_lines = 800
model = "sonnet"
```

```toml
# ~/ninjudd/projector/.projector.toml — this repository only
[review]
model = "fable"
```

The `[review]` table is where identity and review policy live together, so
`review.username` sits beside `review.allow_approve` rather than floating at
the top level as though `operator` might join it.

Together those resolve `review.summarize_min_lines` to `800` and `review.model` to
`fable`. Arrays replace rather than append.

Read one value with a dotted key, which reaches into a table:

```sh
project config get review.summarize_min_lines
project config get review.summarize_min_lines --default 400
```

`get` exits `1` when the key is unset and no `--default` is given, so a caller
can branch on the exit status rather than parse the output. Print everything,
or the files that contributed:

```sh
project config list
project config paths
```

Add `--json` to any of them. `get --json` and `list --json` report which file
each value came from, which is the quickest way to find out why a setting is
not what you expected:

```sh
project config get review.summarize_min_lines --json
```

```json
{
  "key": "review.summarize_min_lines",
  "schema_version": 2,
  "source": "/Users/you/ninjudd/.projector.toml",
  "value": 800
}
```

Keys are not validated. Any key a skill or a script agrees on works, so this
stays useful for settings Projector itself knows nothing about.

These are the keys Projector reads today:

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `projects.dir` | string | `docs/projects` | every command, unless `--projects-dir` is given |
| `instructions.enabled` | boolean | `true` | `init` and `check`, to manage the Projector section in `AGENTS.md` and `CLAUDE.md` |
| `site.enabled` | boolean | `true` | `init`, to set up the GitHub Pages site and its workflow unless `--site` or `--no-site` says otherwise |
| `site.prepare` | string | none | `site build` and `site serve`, as a shell command to run in the checkout before building, once allowed with `--allow-prepare`, unless `--prepare` or `--no-prepare` says otherwise |
| `review.username` | string | the authenticated user | `review-changes` and `start-review-loop`, as the GitHub login that posts reviews |
| `review.allow_approve` | boolean | `false` | `review-changes` and `project review publish`, to permit a real `APPROVE` on a clean cross-author review |
| `review.publish_rule` | boolean | `true` | `init`, to add the Claude Code permission rule for `project review publish` to `.claude/settings.json` |
| `review.gate` | string | none | `project review gate`, as the repository's validation gate: a shell command run in the review's scratch worktree with the worktree and the merge base as `$1` and `$2` |
| `review.summarize` | boolean | `true` | `review-changes`, to publish a summary of a large pull request to the repository's Projector site after each review |
| `review.summarize_min_lines` | integer | `400` | `review-changes`, as the added and deleted lines at which a pull request gets a summary |
| `fix.resolve_human_threads` | boolean | `true` | `start-fix-loop`, to resolve a person's review thread once its fix is pushed; `false` replies and leaves resolving to the reviewer |

`review.allow_approve` is off unless it is exactly `true`; an unset key means
`false` rather than a question to ask. It never applies to a review of your own
pull request, where GitHub refuses the verdict regardless.

The operator -- the account whose branches carry fixes and whose token pushes
them -- is deliberately not a key. It follows whichever token is authenticated,
because both loops act through that token and a file naming a different
account could only disagree with it. Which pull requests a loop watches is not
an identity question either: each watcher reads its tracked set from a file of
`owner/repo#number` lines that the conversation maintains, and sees nothing
outside it.

## Upgrade

`upgrade` moves the installed command and the host plugins to what they were
installed from, from any directory, with the installer's own targets:

```sh
project upgrade          # all, the installer's default
project upgrade cli
project upgrade status
```

How it upgrades depends on where pip recorded that the command came from:

- **A release**, installed by `curl -fsSL https://projector.bot/install.sh |
  bash`, from a release's source archive, or from a Git URL: `upgrade`
  downloads the installer from the newest release and runs it with the same
  targets, which reinstalls from the newest release without git. It prints
  the equivalent `curl ... | bash -s -- <targets>` command on stderr. The
  installer comes from `https://projector.bot/install.sh`, or, for a command
  installed from a GitHub fork, from `install.sh` at `PROJECTOR_REF` (default
  `v0`) in that fork, with `PROJECTOR_REPO` set to the fork.
  `PROJECTOR_INSTALLER_URL` names another installer to run.
- **A checkout**: `pipx` installs a copy of the source, so a checkout that
  moves on leaves the installed command and plugins behind. `upgrade` runs
  the checkout's `install.sh`, prints that command on stderr, and ends with a
  row saying whether the checkout is behind its upstream; a `repo-behind` row
  means the command just built is older than main, so pull and run `upgrade`
  again.

Either way `upgrade` exits with the installer's status, and targets are the
installer's to validate: an unknown one is its usage error, exit 64. See
[the plugin guide](plugins.md) for what each target does. `upgrade` exits 69
when the command is not an installed distribution, when the record of its
source is missing, when a checkout or its `install.sh` no longer exists, or
when the released installer cannot be downloaded.

`upgrade` has no `--json` mode because the installer owns the output.

## Build the Projector site

A repository that sets up the Projector site serves it from GitHub Pages,
built from content Projector keeps in the repository: its `README.md`, the
Markdown documents under `docs/`, the project plans, and the pull request
summaries on the hidden ref `refs/projector/summaries`. The
`summarize-changes` skill writes a summary's data, a spec, and these
commands do everything else:

```sh
project summary init --repo OWNER/NAME --pr 66 --spec summary.json
project summary publish --spec summary.json
project site page --spec summary.json --out site
project site build --out _site --summaries specs/summaries
project site serve --port 8000
project site status --repo OWNER/NAME --pr 66
project site workflow --write
```

`summary init` writes a skeleton spec with every changed file in one
unassigned group. `summary publish` fetches the pull request's diff, checks
that the spec builds against it, commits the spec and the diff to the hidden
ref without touching the checkout, and starts the repository's site workflow.
Pass `--diff` to publish a diff you produced instead of fetching one; it must
run from the spec's `pr.base` to its `pr.head`, because every later deploy
serves the stored diff as it is. Pass `--no-dispatch` to skip the workflow.
When the site is hosted and you have posted a Projector review of the spec's
head, `summary publish` then makes `Summary of this head: <url>` that
review's last line, replacing an older link line rather than repeating it,
and re-reads the review to confirm the link. With no such review, it prints
where the summary is and edits nothing.
It sends two `repository_dispatch` events, `projector-summaries` and
`projector-walkthroughs`, because a site workflow written before summaries
were renamed listens only for the second; a later release stops sending it.

A repository that published before the rename keeps its specs on
`refs/projector/walkthroughs`, under `walkthroughs/`. When the remote has
that ref but no `refs/projector/summaries`, `summary publish` first creates
the new ref from it by replaying each of the old ref's commits with its
`walkthroughs/` folder as `summaries/`, keeping the commit's author, dates,
and message, so every spec keeps the date the site orders a pull request's
heads by. It pushes that history with the new spec on top and leaves the old
ref in place; delete it
with `git push origin :refs/projector/walkthroughs` once every site that
reads it runs a release that reads the new ref. Until the new ref exists,
`site serve` and the site action read the old one.

`site page` builds one summary into a directory you can open from disk or
publish as a Claude Artifact. It refuses when the pull request has moved past
the spec's head, unless `--at-head` asks for the recorded head; pass `--diff`
when GitHub cannot serve a diff that large.

`site build` builds the whole site from a checkout, this repository unless
`--repo-root` names another, and the summary specs under `--summaries`
when there are any. Its menu has three sections. **Projects**, the home page,
groups the projects by status, and opens each one beside a sidebar of its
top-level project's folder: every supplemental file, subdirectory, and nested
project. **Reviews** lists the summaries, and marks a pull request whose base
branch is another open pull request's head with that pull request's number,
linked to its review when the site has one and to GitHub otherwise. A spec
that did not record the number has it looked up with `gh` at build time;
when `gh` cannot answer, the review shows no stack. **Docs** renders `README.md`
beside a sidebar of every other document under `docs/`, leaving out the
projects directory, which Projects covers. Each sidebar lists its readme as
**Overview** and appears only when there is more than that readme to list, and
the menu button at the start of the header shows and hides it. A repository
with no projects opens on Docs instead. A document, in Docs or in a project, is
a Markdown file or an HTML page. The build copies those files under `content/`
and describes them in `site.json`; the page renders Markdown in the browser and
shows an HTML page as it is, titled by its `<title>`, in a frame that fills the
window below the header, with the sidebar hidden until the menu button opens
it. A full-screen button at the end of the header hides the header, giving
the page the whole window, and a button in the corner leaves full screen.
The frame loads the page's copy under `content/`, beside a copy of every
other file under `docs/` and the projects directory, so the data, scripts,
and WebAssembly modules the page loads by relative path resolve there. The
address's hash passes into the frame and follows it back out, so a deep link
into a hash-routed page works. HTML is served as the repository wrote it,
unlike Markdown, which the page sanitizes with DOMPurify: an HTML page runs
unsandboxed on the site's origin, with the same reach as the site's own
scripts, so it carries the same trust as code merged to the default branch.
Every view has its own path: `projects/<name>/` for a project and
`projects/<name>/<file>/` for each of its files, at its path inside the
project without `.md` or `.html`; `reviews/`, `reviews/<number>/` and
`reviews/<number>/<head>/` for summaries; and `docs/` for the README and
`docs/<path>/` for each document at its repository path without `.md` or
`.html`. An `index.html` takes its folder's path when no readme does. A
`README.md` at the top of `docs/` moves to `docs/readme/`, since the
repository README holds `docs/`. A file named like a folder beside it, such as
`notes.md` next to `notes/`, keeps its whole name in its path, `notes.md/`,
because the folder takes `notes/`; an `index.html` beside a readme moves to
`index/`.
Pass `--base` with the path the site is served under, such as `/projector/`
for a project site, so every page links to the others and to the shared
assets under `assets/`; it defaults to `/`. A review links to the projects
its diff changes, and to any its spec names in a `projects` list, and each
project page lists its reviews. `search/` searches every document the site
serves, from a `search.json` index the build writes, and non-Markdown files
under `docs/`, such as images, are copied into the site so a relative link to
one resolves there.
A broken plan costs only its own place in the Projects section, never the
build: the site lists every project that loads and prints a
`::warning title=Project skipped::` line for each plan it leaves out, naming
the path and the problem `project check` reports for it. That covers a plan
that does not parse, a top-level folder with no lowercase `readme.md`, and an
entry point spelled `README.md`. The site workflow runs `site build` through Projector's
composite action with `--check-visibility`, which refuses to build, and so to
deploy, when a private repository's Pages site is public or GitHub cannot say
whether it is. For summaries it builds every
`<number>/<head>/spec.json` against the `diff.patch` beside it, asking GitHub
for nothing, and skips and reports any spec that fails or has no stored diff.
Each site page loads its `data.json` when it opens, where a `site page` embeds
its data so it opens from disk.

For pages or files the repository generates rather than commits, `site build`
first runs the shell command `site.prepare` names, in the checkout, through
the system shell, and says so on stderr. `--prepare` runs a given command in
its place and `--no-prepare` skips it. A command that fails stops the build
with exit status 65 before anything is built. The action passes its `prepare`
input as `--prepare`, and runs the build after its own checkout, which
removes untracked files, so what the command generates survives to the copy;
set up the command's toolchain in steps before the action.

`site.prepare` comes from the checkout, so a branch someone else wrote, or a
clone of a repository you have never read, could otherwise run its own shell
command as you the moment you preview its docs. On your machine the command
runs only once you allow it: pass `--allow-prepare` to `site build` or
`site serve`, and Projector remembers that exact command for that checkout
until the command changes. Until then it prints the command and builds the
site without it. The record is a list of hashes in
`$XDG_STATE_HOME/projector/allowed-prepare`, or
`~/.local/state/projector/allowed-prepare`, outside every checkout and its
configuration. A deploy (`GITHUB_ACTIONS=true`) runs the command unasked,
since the site workflow builds only what was merged to the default branch,
and a `--prepare` given on the command line needs no allowing. Allowing covers
the command's text, not the files it runs: once `make explorer` is allowed, a
branch that changes the Makefile's `explorer` target runs that change, as
running `make` there would.

`site serve` builds the site from a checkout, as `site build` does, into a
temporary directory and serves it over HTTP until you press Ctrl-C, or it
receives SIGTERM or SIGHUP, which removes the directory. It needs no GitHub
Pages site and no workflow. It listens on `127.0.0.1:8000` unless `--host`
or `--port` says otherwise, and warns when `--host` reaches beyond this
machine, because anyone who can reach the address can read the site. It
answers 403 to a request whose `Host` header names anything but `localhost`,
a loopback address, or the `--host` given, so a web page cannot read the
site through DNS rebinding; a wildcard `--host` such as `0.0.0.0` answers
any name. `--port 0` picks a free port and prints it. It runs an allowed
`site.prepare`, or `--prepare`, before the first build and before each
rebuild, and takes the sources' fingerprint after the command, so a file the
command rewrites does not start another rebuild; `--no-prepare` skips it.
Before building, it fetches the summaries ref from `--remote`, `origin`
by default, into the same `refs/projector/remotes/<remote>/summaries`
copy that `summary publish` keeps. When the fetch fails it says why and
serves the summaries already fetched; `--no-fetch` skips the fetch, and
`--summaries` serves a directory of specs instead of the ref. While it
runs it checks `README.md`, `docs/`, a configured `projects.dir`, and the
summaries every second and rebuilds when any of them change;
`--no-watch` builds once. A path the build has no file for gets `404.html`
with status 404, as GitHub Pages answers it. `--base` serves the site under
a path, as `site build` builds it.

`site status` exits 0 and prints the site's URL, or with `--pr` the pull
request's review URL, when the repository has the site workflow on its
default branch and a GitHub Pages site. It exits 3 and says why when either is
missing, or when a private repository's site is public. `site workflow`
prints the workflow file a repository adds to its default branch once, or
writes it with `--write`. `--write` refuses, exiting 1, to overwrite a
workflow edited by hand, and `--force` replaces it anyway. The workflow runs
when a summary is published, when a push to the default branch changes
`README.md`, `docs/`, a configured `projects.dir` outside `docs/`,
`.projector.toml`, or the workflow itself, and on demand; `--branch` names the
default branch when `origin` does not record it.

## Review a pull request

`project review` runs the mechanical steps of the `review-changes` skill, so
a review loop does not rebuild them as helpers of its own:

```sh
project review setup 66 --model claude-opus-5-5 --loop main-loop
project review move 66
project review census 66 --json
project review publish 66 --verdict clean --body body.md --covered 12/12 --loop main-loop
project review release 66
project review gate 66
```

`setup` checks that pull request 66 is open, fetches its head into the
checkout — from its branch, or from `pull/66/head` when the head is in a fork —
and fetches the base. It creates a scratch worktree at the exact SHA GitHub
reports, verifies the worktree's `HEAD`, computes the merge base, and posts the
start comment the skill describes as the reviewer. It records the SHA, the
merge base, the worktree, whether the head is trusted, and the start comment's
id and `created_at` in a state file, and prints them. A head is trusted when it
is a branch in this repository, the pull request's author is you or has write
access, and no commit names another author; a fork's head, or an untrusted
one, is recorded so and reviewed by reading. `--rereview` records a re-review
of a head the loop already published a verdict on.

The start comment and the review's signature line name the model from `--model`
and the reasoning effort from `CLAUDE_EFFORT` in the environment, which Claude
Code sets for the Bash tool and keeps current through a mid-session `/effort`
change. When `CLAUDE_EFFORT` is unset or empty, as on Codex or on a model
without effort support, they leave the effort out rather than guess it.

`move` follows a head that moved mid-review: it creates a worktree for the new
head, rechecks trust, edits the start comment in place to name it, re-reads the
comment to confirm the edit, and updates the state file. `census` lists every
thread whose first comment carries the `projector-finding` marker, reading
every page, and prints how many are resolved and open and each open thread's
`path:line`; with `--json` each thread also carries its comments. `release`
clears the pull request's review lock.

`publish` submits the review. `--body` names a file holding the review below
its signature line, with `{census}` where the census goes and, wherever used,
`{took}`, `{seconds}`, `{sha}`, and `{short_sha}`, which it fills only outside
quoted code; `--threads` names a JSON list
of findings, each `{"path", "line", "priority", "body"}`; `--covered` gives the
files read over the files changed. `publish` writes the signature line and
marker itself from the review's state and one reading of the clock, and adds
the finding marker to each thread. It refuses, exiting 1 and keeping the lock,
when the pull request closed or its head moved, when a finding's line is
outside the diff's hunks or its text lacks a priority header or a `**Fix:**`
line, when the body is empty, carries its own signature or marker comment, or
has no `{census}` outside code, when the verdict disagrees with the finding
count, or when the reviewer already has a verdict on the head that this loop's
record does not account for; outside a loop, `--second-verdict <review-id>`
publishes beside an earlier verdict the body names. On a self-review it
submits a `COMMENT` and then marks the pull request ready or returns it to
draft; on another author's pull request it posts `REQUEST_CHANGES` or a
`COMMENT`, or an `APPROVE` where `review.allow_approve` is `true`, and leaves
draft state alone. It then re-reads the review and the draft state, adds the
review id to the loop's record, deletes the start comment, and releases the
lock. A run that fails after submitting exits non-zero, and running it again
finishes that review rather than posting another.

`gate` runs the repository's validation gate: the shell command
`review.gate` names in `.projector.toml`, run with `sh -c` in the scratch
worktree, with the worktree and the merge base as `$1` and `$2` and as
`PROJECTOR_WORKTREE` and `PROJECTOR_BASE`. Its output passes through, on
standard error under `--json` so standard output carries only the JSON record;
`gate` exits with the command's status, and the state file records the
command, its exit status, the head, and when it ran. The command runs the
head's code, so `gate` refuses, exiting 1 without running anything, on a head
`setup` recorded as untrusted, whether or not `review.gate` is set, and when
`review.gate` is unset.

No command takes a SHA: each comes from GitHub or the state file. `setup` works
from the checkout containing the working directory, or `--checkout`, and
refuses outside one; the repository is that checkout's `origin`, and `--repo`
must name the same one. Every later command reads the checkout, worktree, and
repository from the state file, finding it by `--repo` or by the pull request's
number, so it runs from any directory. A command that posts runs `gh` as the
reviewer, `--reviewer` or `review.username` or else the authenticated user,
with `GH_TOKEN` set to `gh auth token --user <reviewer>` for that call alone,
so the account the rest of the session uses is left alone.

A review holds a lock for its pull request and head from `setup` until it is
published, recording the host, the loop, and when it was taken, so a second
instance for the same head exits. A refused `setup` releases it, a lock older
than a day is stale, and `release` clears one left by a session that stopped.
`--loop <id>` names the review loop; each loop keeps its record of published
reviews under that id. State, locks, loop records, and scratch worktrees live
under `$XDG_STATE_HOME/projector/reviews`, or
`~/.local/state/projector/reviews`, and `--state-dir` moves them. Every refusal
exits 1 and says what to do.

## Consume JSON

Pass `--json` to `init`, `list`, `show`, `search`, `create`, `status`,
`priority`, `done`, or `check`. Responses use stdout only for JSON and include
`"schema_version": 2`.

`init --json` keeps the `action` and `path` of the projects README at the top
level, as it always has, and adds `files` with every file it manages:

```json
{
  "action": "unchanged",
  "files": [
    {"action": "unchanged", "path": "docs/projects/README.md"},
    {"action": "updated", "path": "AGENTS.md"},
    {"action": "created", "path": "CLAUDE.md"}
  ],
  "path": "docs/projects/README.md",
  "schema_version": 2
}
```

`check --json` reports `"valid": true` when no issue is an error; each issue
carries `"severity": "error"` or `"severity": "warning"`.

For example:

```console
$ project list --priority now --json
{
  "projects": [
    {
      "name": "projector",
      "owner": null,
      "path": "docs/projects/projector/readme.md",
      "priority": "now",
      "status": "in-progress",
      "title": "Turn agent-config into Projector"
    }
  ],
  "schema_version": 2
}
```

Projector uses these exit codes:

| Code | Meaning |
| ---: | --- |
| `0` | The command completed successfully. |
| `2` | Command syntax or an argument is invalid. |
| `3` | `project site status` found no Projector site for the repository. |
| `65` | Project data is invalid or a mutation is unsafe. |
| `66` | The requested project or the projects directory does not exist. |
| `67` | The requested project is ambiguous. |
| `69` | Git, the interactive editor, or the installer is unavailable. |
| `78` | A `.projector.toml` file is not valid TOML. |
