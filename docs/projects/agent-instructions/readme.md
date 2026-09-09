---
status: ready
priority: now
---

# Seed Projector conventions into agent instructions

## 1. Outcome

A repository that adopts Projector carries Projector's conventions in the
instruction files coding agents read. Every session in that repository then
follows them: sessions that never invoke a Projector skill, sessions under
Claude Code and under Codex, and sessions run by collaborators who have not
installed the plugin. The text lives in the repository, so a person reading
`AGENTS.md` on GitHub sees it and a change to it is reviewed like any other.
`project init` writes the text and refreshes it, and `project check` reports
when it has drifted from the text the installed CLI ships.

The conventions used to reach every session by accident of installation.
Commit 6bcaa38 symlinked this repository's `AGENTS.md` to `~/CLAUDE.md`, which
Claude Code concatenates into every session under the home directory, and to
`~/.codex/AGENTS.md`, which Codex reads globally. Pull request #27 removed those
links on purpose, so that installing Projector never replaces host
configuration, and the writing register adopted in #18 went with them. Today
the register exists only in this repository's `AGENTS.md`, and even here Claude
Code does not load it, because the repository has no `CLAUDE.md` to import it.
A README written in another repository on 2026-09-09 without the register is
the gap this project closes.

## 2. The generated block

Projector owns one contiguous block in the repository's root `AGENTS.md`.
Everything between the markers belongs to Projector; everything outside them
belongs to the repository and is never rewritten.

The markers are HTML comments, so GitHub renders nothing for them:

```markdown
<!-- projector:begin 1 -->
## Projector conventions

...
<!-- projector:end -->
```

The integer in the opening marker is the template version. It changes only
when the block's text changes, never when the CLI releases for another reason.
A file holds at most one block.

Codex reads root `AGENTS.md` natively. Claude Code reads only `CLAUDE.md`, so
the repository also needs a root `CLAUDE.md` containing the line `@AGENTS.md`,
which Claude Code's import syntax follows. That one line is the only content
Projector ever writes to `CLAUDE.md`.

The block covers Projector's repository-level conventions, not only the writing
register, because an agent that does not know the repository uses Projector has
the same gap as one that does not know the register:

- Where plans live, that `<projects dir>/README.md` states the format, that
  the `project` command is how to find and change projects, and that
  `project check` runs before work is handed over.
- The documentation register from this repository's `AGENTS.md`: new and
  substantially revised documentation uses the Google developer documentation
  style, `docs/` describes the behavior on the current branch and changes with
  it, and existing documents keep their register until a substantial revision.
- The GitHub register: issues and review comments use the same style; a pull
  request body is prose that says why the change exists, because a squash
  merge makes it the commit message; Markdown edits keep the wrapping around
  them; GitHub prose is not hard-wrapped, because GitHub renders line breaks.
- One sentence saying that Projector generates the section and that the way
  to change it is to change Projector and run `project init`.

The chat register from #18 stays out. How an agent talks to the person driving
it is that person's preference, and belongs in their own instructions.

The text is a template at `src/projector/templates/agents-block.md`, including
its markers, with one placeholder for the projects directory so a repository
that sets `projects.dir` in `.projector.toml` reads the right path. The
rendered block is the same string for `init` and `check`; both call one
function.

## 3. `project init` writes and refreshes the block

`init` becomes idempotent. It still creates `<projects dir>/README.md` when
that file is absent, and it now reports an existing one as unchanged instead
of failing. It then manages two more files at the repository root:

1. `AGENTS.md`. When the file is absent, `init` creates it containing only the
   block. When the file has no block, `init` appends one blank line and the
   block. When the file has a block, `init` compares the marker version with
   the template version: an older or equal version is replaced in place with
   the rendered template; a newer version is left exactly as it is, reported
   as `kept`, with a note on stderr to upgrade the `project` command. A file
   with a begin marker and no end marker, or with two blocks, is reported as
   malformed and not touched.
2. `CLAUDE.md`. When the file is absent, `init` creates it containing
   `@AGENTS.md`. When the file exists and does not already import
   `AGENTS.md`, `init` appends one blank line and `@AGENTS.md`. A file imports
   `AGENTS.md` when an `@AGENTS.md` or `@./AGENTS.md` token appears anywhere
   outside a Markdown code span or fenced code block, which is the test Claude
   Code's own import parser applies, so `init` and `check` never disagree with
   the host about whether the import exists. A mention inside backticks is
   literal text to the host and to Projector alike.

`init` resolves both paths with symlinks followed and writes to the file a
link points at, never to the link itself, so a linked `AGENTS.md` or
`CLAUDE.md` stays a link rather than becoming a copy of its target. The
existing `_atomic_write` cannot be reused as it stands: `os.replace` on a
symlink swaps the link for a regular file while `path.stat()` follows the link
and passes the signature check.

When the two paths resolve to one file, in either direction, `init` writes the
block once into that file and writes no import line. Claude Code and Codex
already read the same text, and an `@AGENTS.md` line would be a self-import
for one host and a literal line in what the other reads. The pair is reported
under `AGENTS.md` with the block's action and under `CLAUDE.md` as
`unchanged`. `check` treats a same-file pair as satisfying
`claude-import-missing`. A `CLAUDE.md` that is a symlink to `AGENTS.md` is one
instance of this rule; a repository that adopted `CLAUDE.md` first and linked
`AGENTS.md` to it later is the other.

Bytes outside the block are preserved exactly, including line endings and
trailing whitespace. Each written file ends with one newline.

Human output prints one line per file, `<action> <path>`, with actions
`created`, `updated`, `unchanged`, or `kept`. JSON output keeps the top-level
`action` and `path` that `init` has always reported for the projects README,
so a consumer written against the current shape keeps working, and adds
`files` beside them:

```json
{
  "schema_version": 2,
  "action": "unchanged",
  "path": "docs/projects/README.md",
  "files": [
    {"path": "docs/projects/README.md", "action": "unchanged"},
    {"path": "AGENTS.md", "action": "updated"},
    {"path": "CLAUDE.md", "action": "created"}
  ]
}
```

`unchanged` is a new value for the top-level `action`; before this change an
existing README was an error rather than a result.

`init` exits 0 after any run in which it wrote what it safely could, including
a run that kept a newer block. A malformed block is a `ProjectorError` and
exits like other data errors, after the other files have been handled.

A repository that does not want Projector in its instruction files sets:

```toml
[instructions]
enabled = false
```

With that key false, `init` manages only the projects README and `check`
reports none of the issues in section 4.

## 4. `project check` warns on drift

`Issue` gains a `severity` field with the values `error` and `warning`.
Existing issues are errors. The exit code and the `valid` field follow errors
alone, so a repository with only warnings exits 0 and reports
`Project plans are valid.` on stdout after the warnings. Warnings print to
stderr as `warning: <path>: <message> [<code>]`, and JSON issues carry
`"severity"`.

The instruction issues are all warnings, so a collaborator on an older CLI is
told what to do without a failing gate:

| Code | Condition | Message names the fix |
| --- | --- | --- |
| `instructions-missing` | `AGENTS.md` is absent or has no block | run `project init` |
| `instructions-outdated` | marker version is older than the template | run `project init` |
| `instructions-ahead` | marker version is newer than the template | upgrade the `project` command |
| `instructions-edited` | same version, content differs from the rendered template | run `project init` to restore it, or change the template in Projector |
| `instructions-malformed` | begin marker without end marker, or more than one block | repair the markers by hand |
| `claude-import-missing` | `CLAUDE.md` is absent or does not import `AGENTS.md` as section 3 defines it, and does not resolve to the same file as `AGENTS.md` | run `project init` |

`instructions-missing` fires in every repository that adopted Projector before
this change. That is the intended nudge, and one `project init` clears it.

## 5. One source for the text

The template version lives in the template's own begin marker, so the text and
its version cannot be edited apart. A test pins the SHA-256 of the template
per version: changing the text without bumping the version fails with a
message naming the bump. A second test renders the template and asserts that
the string `init` writes is the string `check` compares against.

This repository adopts the block. `AGENTS.md` loses its
"Write current documentation" section, which the block now states, and gains
the block at its end. The repository gains a `CLAUDE.md` containing
`@AGENTS.md`, so Claude Code sessions here read the contributor instructions
for the first time. `project check` here then guards the block like any other
adopted repository, and the validation gate in `AGENTS.md` already runs it.

## 6. Documentation and skills

- `docs/cli.md`: rewrite "Adopt a repository" for an idempotent `init` that
  manages three files, document the `files` list and the new `unchanged`
  action in "Consume JSON", and add the `instructions.enabled` key to "Read
  configuration". Extend "Validate
  projects" with severities, the six warning codes, and the exit-code rule.
- `README.md`: "Adopt Projector in a repository" names `AGENTS.md` and
  `CLAUDE.md` among the files `init` writes and says why.
- `docs/plugins.md`: one paragraph saying that skills load only when invoked
  and that the block is how conventions reach every session, so a reader does
  not look for a hook or a plugin `CLAUDE.md`.
- `plan-project`, `work-project`, and `finish-project` skills: the validation
  step says to resolve any warning `project check` prints, and that
  `project init` refreshes a stale Projector section.

## 7. Implementation sequence

1. Add the template, the render function, and block parsing (find markers,
   read the version, detect malformed files) in a new
   `src/projector/instructions.py`, with the hash-pin test and parsing tests.
2. Make `init` idempotent over the projects README, then add the `AGENTS.md`
   and `CLAUDE.md` steps and the `files` output. Update the existing `init`
   tests, which assert the old refusal, and add the cases in section 8.
3. Add `severity` to `Issue`, the warning output path, and the six instruction
   checks. Add the `instructions.enabled` opt-out to both commands.
4. Update the documentation and the three skills.
5. Run `project init` in this repository, remove the superseded `AGENTS.md`
   section, and confirm `project check` prints nothing but
   `Project plans are valid.`

Steps 1 through 3 are one reviewable argument and ship together. Steps 4 and 5
belong in the same pull request, because the repository should not merge a CLI
whose own check warns about the repository.

## 8. Acceptance criteria

Verified in a temporary Git repository unless stated otherwise:

- In an empty repository, `project init` creates `docs/projects/README.md`,
  `AGENTS.md`, and `CLAUDE.md`; `project check` prints only
  `Project plans are valid.` and exits 0; a second `project init` reports
  every file `unchanged` and changes no bytes.
- With a pre-existing `AGENTS.md` and `CLAUDE.md` of arbitrary content, `init`
  appends the block and the import line, and every byte before them is
  unchanged.
- Editing one word inside the block makes `check` warn `instructions-edited`
  with exit 0; `init` restores it and reports `updated`.
- Raising the marker version by hand above the template makes `check` warn
  `instructions-ahead`; `init` reports `kept` and the file is byte-identical
  afterwards.
- Lowering the marker version makes `check` warn `instructions-outdated` and
  `init` replace the block.
- Deleting the end marker makes `check` warn `instructions-malformed` and
  `init` exit nonzero without touching `AGENTS.md`, after writing the other
  files.
- Deleting `CLAUDE.md` makes `check` warn `claude-import-missing`.
- A `CLAUDE.md` that reads `Read @AGENTS.md before making changes.` or uses
  `@./AGENTS.md` gets no second import from `init` and no warning from
  `check`; one that mentions `@AGENTS.md` only inside backticks or a fenced
  block gets the import appended and warns until it does.
- With `CLAUDE.md` a symlink to `AGENTS.md`, and separately with `AGENTS.md` a
  symlink to `CLAUDE.md`, `init` writes the block once into the shared file,
  both paths are still links afterwards, no `@AGENTS.md` line is written,
  `check` produces no warning, and a second `init` reports both paths
  `unchanged`.
- Setting `projects.dir` renders that path inside the block; setting
  `instructions.enabled = false` makes `init` manage only the README and
  `check` report no instruction issue.
- `check --json` carries `"severity"` on every issue and `"valid": true` when
  only warnings are present; a real error still exits 65.
- Changing the template text without bumping its version fails the test suite.
- In this repository, `project check` is clean, `CLAUDE.md` exists, and a new
  Claude Code session answers a question about the documentation style with
  the Google register.
- In a repository adopted this way, a Codex session sees the block without any
  plugin installed, because it reads `AGENTS.md` natively.
- The full validation gate in `AGENTS.md` passes.

## 9. Decisions

- **Generate a block in `AGENTS.md` rather than link to a file.** Claude Code
  can import a file from a home path, but Codex has no import syntax and reads
  only concatenated `AGENTS.md` files, and a committed path into a home
  directory contradicts the portability rule at the top of `AGENTS.md`. A
  block is the only mechanism that reaches both hosts from the repository.
- **No SessionStart hook.** A plugin hook fires in every Claude Code session
  and could gate on `.projector.toml`, but only collaborators with the plugin
  get it, the text is invisible in the repository, hook output ordering
  relative to `CLAUDE.md` is undocumented, and Codex plugin-local hooks are
  reported not to run (openai/codex#16430). Nothing here prevents adding one
  later; nothing here needs one.
- **Warnings, not errors.** A collaborator on an older CLI must not fail a
  gate they cannot fix without upgrading. The version in the marker lets each
  message name the right fix, and `init` never downgrades a newer block.
- **`init` is the update verb.** Rerunning the adoption command to bring a
  repository up to date is one thing to learn, and every warning can name it.
  A separate `project sync` was rejected as a second name for the same act.
- **An integer template version in the marker.** A content hash cannot say
  which side is newer; the CLI version would mark every repository stale on
  every release; a version that lives only in code can drift from the text.
- **Whole conventions, not only the register.** See section 2. The chat
  register is excluded as a personal preference.
- **HTML comment markers appended at the end.** Comments are invisible on
  GitHub, and appending avoids guessing where in a repository's own
  instructions the block belongs.
- **One import line in `CLAUDE.md`, nothing more.** Claude Code's own
  documentation recommends a `CLAUDE.md` that imports `AGENTS.md` for
  repositories that serve more than one agent. Projector never writes
  instructions into `CLAUDE.md` directly, so a repository that keeps its own
  `CLAUDE.md` content is not disturbed.
- **Recognize an import where Claude Code does.** The host accepts `@path`
  anywhere outside code spans and fences, inline in a sentence included, and
  resolves `@./AGENTS.md` the same as `@AGENTS.md`. An exact-line test would
  make `init` append a second import to a file the host already reads, and
  whether the host dedupes a file imported twice is undocumented. Matching
  the host's rule costs a small parser and removes the disagreement.
- **Opt-out through configuration.** `instructions.enabled = false` is the
  documented way to keep Projector out of a repository's instruction files;
  deleting the block and living with a warning is not.
- **Extend `init --json` under schema version 2 instead of replacing it.**
  `init` shares one `{"action", "path"}` shape with `create`, `status`,
  `priority`, and `done`, and `json_text` stamps every command with the one
  version `docs/cli.md` tells consumers to check. Replacing the shape would
  hand a consumer reading `.path` a `null` with no version change to warn it;
  bumping the version to 3 would break consumers of every command for one
  command's addition. Keeping `action` and `path` for the README and adding
  `files` beside them is additive, so the version stays at 2.

## 10. Open questions

None block implementation. Whether the Codex version installed today runs
plugin-local hooks is unverified and does not matter to this design. What
Claude Code does with a `@` import whose target is missing is undocumented and
does not arise, because `init` writes both files.
