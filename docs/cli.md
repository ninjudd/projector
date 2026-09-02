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

## Adopt a repository

Run `init` once to create `docs/projects/README.md`:

```sh
project init
```

If projects already exist but the convention file is missing, `init` adds only
that file. It refuses to replace an existing convention file.

Every command except `init` and `check` requires that directory. When it is
missing, Projector names the path it looked for and exits 66 instead of
reporting an empty repository:

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
filesystems.

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
reviewer = "minjudd"

[review]
effort = "xhigh"
model = "sonnet"
```

```toml
# ~/ninjudd/projector/.projector.toml — this repository only
[review]
model = "fable"
```

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
| `reviewer` | string | the authenticated user | `start-review-loop`, as the identity that posts reviews |
| `review.allow_approve` | boolean | `false` | `start-review-loop`, to permit a real `APPROVE` on a clean cross-author review |

`review.allow_approve` is off unless it is exactly `true`; an unset key means
`false` rather than a question to ask. It never applies to a review of your own
pull request, where GitHub refuses the verdict regardless.

The operator -- the account whose pull requests are watched and whose branches
carry fixes -- is deliberately not a key. It follows whichever token is
authenticated, because a file naming a different account would scope a loop to
pull requests it cannot push to and then go quiet, which both loops read as
nothing outstanding.

## Consume JSON

Pass `--json` to `init`, `list`, `show`, `search`, `create`, `status`,
`priority`, `done`, or `check`. Responses use stdout only for JSON and include
`"schema_version": 2`.

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
| `65` | Project data is invalid or a mutation is unsafe. |
| `66` | The requested project or the projects directory does not exist. |
| `67` | The requested project is ambiguous. |
| `69` | Git or the interactive editor environment is unavailable. |
| `78` | A `.projector.toml` file is not valid TOML. |
