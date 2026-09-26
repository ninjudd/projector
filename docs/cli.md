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
```

`init` manages three files and reports what it did to each:

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
other files, then exits 65 and names the repair, and prints no JSON document
in `--json` mode.

To keep Projector out of your instruction files, set in `.projector.toml`:

```toml
[instructions]
enabled = false
```

With that key false, `init` manages only the projects README and `check` says
nothing about `AGENTS.md` or `CLAUDE.md`.

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

## Browse projects

Run `list` to group projects by priority without creating an index. Each row
shows the project's status, and completed projects group together at the end:

```console
$ project list
now:
  projector                    in-progress  Turn agent-config into Projector
```

Add `--status ready` or `--priority next` to select one value, or both to
intersect them. Run `show <project>` to print the entry point, including its
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
missing priorities, missing top-level
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
effort = "xhigh"
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

Together those resolve `review.effort` to `xhigh` and `review.model` to
`fable`. Arrays replace rather than append.

Read one value with a dotted key, which reaches into a table:

```sh
project config get review.effort
project config get review.effort --default medium
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
project config get review.effort --json
```

```json
{
  "key": "review.effort",
  "schema_version": 2,
  "source": "/Users/you/ninjudd/.projector.toml",
  "value": "xhigh"
}
```

Keys are not validated. Any key a skill or a script agrees on works, so this
stays useful for settings Projector itself knows nothing about.

These are the keys Projector reads today:

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `projects.dir` | string | `docs/projects` | every command, unless `--projects-dir` is given |
| `instructions.enabled` | boolean | `true` | `init` and `check`, to manage the Projector section in `AGENTS.md` and `CLAUDE.md` |
| `review.username` | string | the authenticated user | `start-review-loop`, as the GitHub login that posts reviews |
| `review.allow_approve` | boolean | `false` | `start-review-loop`, to permit a real `APPROVE` on a clean cross-author review |

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

## Upgrade from the checkout

`pipx` installs a copy of the source, so a checkout that moves on leaves the
installed command and plugins behind. `upgrade` runs the checkout's
`install.sh` from any directory, with the same targets:

```sh
project upgrade          # ./install.sh, which defaults to all
project upgrade cli      # ./install.sh cli
project upgrade status   # ./install.sh status
```

The command finds the checkout in the source pip recorded at install time, so
it needs neither a repository nor a working directory inside one. It prints the
command it runs on stderr, then the installer's own output, and exits with the
installer's status. The install targets end with a row saying whether that
checkout is behind its upstream, and `status` opens with it; a `repo-behind`
row means the command just built is older than main, so pull and run
`upgrade` again. Targets are the installer's to validate: an unknown one is
its usage error, exit 64. See [the plugin guide](plugins.md) for what each
target does.

A command installed from a Git URL rather than a checkout has no `install.sh`
to run, so `upgrade` exits 69 and names the URL. It also exits 69 when the
command is not an installed distribution, when the record of its source is
missing, or when the checkout or its `install.sh` no longer exists.

`upgrade` has no `--json` mode because the installer owns the output.

## Build the Projector site

A repository that sets up Projector hosting serves a Projector site from
GitHub Pages, built from content Projector keeps in the repository. Today
that content is the pull request walkthroughs on the hidden ref
`refs/projector/walkthroughs`; browsing `docs/projects` joins it later. The
`walkthrough-pr` skill writes a walkthrough's data, a spec, and these
commands do everything else:

```sh
project walkthrough init --repo OWNER/NAME --pr 66 --spec walkthrough.json
project walkthrough publish --spec walkthrough.json
project site page --spec walkthrough.json --out site
project site build --walkthroughs specs/walkthroughs --out _site
project site status --repo OWNER/NAME --pr 66
project site workflow --write
```

`walkthrough init` writes a skeleton spec with every changed file in one
unassigned group. `walkthrough publish` checks that the spec builds, commits
it to the hidden ref without touching the checkout, and starts the
repository's site workflow; pass `--no-dispatch` to skip the workflow.

`site page` builds one walkthrough into a directory you can open from disk or
publish as a Claude Artifact. It refuses when the pull request has moved past
the spec's head, unless `--at-head` asks for the recorded head; pass `--diff`
when GitHub cannot serve a diff that large. `site build` builds every
`<number>/<head>/spec.json` under a directory into the whole site, skipping
and reporting any spec that fails; the site workflow runs it through
Projector's composite action.

`site status` exits 0 and prints the site's URL, or with `--pr` the pull
request's walkthrough URL, when the repository has the site workflow on its
default branch and a GitHub Pages site. It exits 3 and says why when either is
missing, or when a private repository's site is public. `site workflow`
prints the workflow file a repository adds to its default branch once, or
writes it with `--write`.

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
