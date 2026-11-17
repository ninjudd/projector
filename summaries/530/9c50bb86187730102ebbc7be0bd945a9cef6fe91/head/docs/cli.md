# Use the Projector CLI

Projector installs one command, `project`. It discovers the Git root from your
current directory and reads projects under `docs/projects/`. A command that
reads plans takes `--root <path>` to select a repository explicitly, and
`--projects-dir <path>` to use another project-plan directory; the `config`
commands take `--root` too.

The command comes first, and every option goes after it, as in
`project list --root ../other`. `project` refuses an option before the
command, `--` and `--help` included, and exits 2. That keeps each command's
permission rule matching every way to run it, as
[Adopt a repository](#adopt-a-repository) describes.

Running the package as a module is equivalent when the command is not on your
`PATH`:

```sh
python3 -m projector check
```

Print the version of the installed command, the list of commands, or one
command's help:

```sh
project version
project help
project help review publish
```

## Adopt a repository

Run `init` from anywhere inside a Git repository:

```console
$ project init
created docs/projects/README.md
created AGENTS.md
created CLAUDE.md
updated ~/.claude/settings.json
```

`init` manages three files in the repository and two of yours, and reports
what it did to each:

- `docs/projects/README.md` states the convention, links to the Projector
  repository, and shows how to install the CLI, so a reader who meets
  `docs/projects/` in another repository knows where the `project` command
  comes from. `init` creates it once and never rewrites it.
- `AGENTS.md` carries a section Projector generates between two HTML-comment
  markers. It says where plans live, how to use `project`, the writing style
  for documentation and GitHub prose, and how to open dependent work as a
  stack with `gh stack`, or as pull requests chained by base branch where
  `gh stack` is unavailable, and keep every layer on the current head of the
  one below so the stack can merge as a unit. Its last part applies only
  where the Projector plugin is installed: build every change with the
  `implement` skill, whose hand-over subscribes the session to fix its pull
  request, open every pull request as a draft and mark it ready for review
  with `project review ready` once its work is done, and, where the `ask`,
  `tell`, and `request` tools are available, post anything the person must
  not miss to the inbox as well as saying it in the conversation. A draft says
  that its author is still working on the pull request, and a review that
  signs off the current head labels the pull request `clean`. It tells an
  agent to post a request only for work it cannot do itself, such as running
  a command it is not allowed to run or checking a change on the person's
  phone, with numbered steps that each say what the person should see, and
  options that fit the work, such as `Looks good` and `Some feedback` for a
  test. It tells an agent to act on the option a person
  chose and the notes under it, and not to post a declined request again. It
  tells an agent to give a request a start action only when it must prepare
  something first, to do the slow part, such as a build, before it posts the
  request so the start is quick, to do the start action only once `STARTED`
  arrives, exactly as that message quotes it, and then to mark the request
  ready or blocked.
  It tells an agent whose question or request the person replied to, rather
  than resolving it, to answer what they asked and then withdraw the item
  with the `withdraw` tool, revise it with the `revise` tool, or leave it
  open, and to withdraw any other item that no longer needs them each time
  the person writes. `init` appends the section to an existing file or creates
  the file with only that section, and refreshes the section when it falls
  behind the template this command ships. Everything outside the markers is
  yours and is never changed.
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
- `~/.claude/settings.json`, your own Claude Code settings, gains
  `Bash(project *)` in `permissions.allow`, which allows every `project`
  command, and these entries in `permissions.ask`, which still ask before the
  few commands that reach outside Projector's own state:

  ```json
  "ask": [
    "Bash(project init *)",
    "Bash(project upgrade *)",
    "Bash(project config set *)",
    "Bash(project config unset *)",
    "Bash(project site *prepare*)",
    "Bash(project inbox answer *)",
    "Bash(project inbox dismiss *)",
    "Bash(project inbox snooze *)",
    "Bash(project inbox done *)",
    "Bash(project inbox decline *)",
    "Bash(project inbox start *)",
    "Bash(project inbox unclear *)",
    "Bash(project inbox discuss *)",
    "Bash(project inbox read *)",
    "Bash(project console)",
    "Bash(project console *)",
    "Bash(project ios *allow-untrusted*)"
  ]
  ```

  `project upgrade` runs an installer it downloads, `project init` writes
  these rules, and `project config set` and `unset` can change a setting that
  widens an agent's authority, such as `review.allow_approve`.
  `project site build` and `serve` run the shell command `--prepare` names, or
  the checkout's own `site.prepare` once `--allow-prepare` allows it, so
  `Bash(project site *prepare*)` asks before either flag, as `--prepare x` or
  `--prepare=x`, and before `--no-prepare` too. Those two commands refuse an
  abbreviated option such as `--prep`, so no shorter spelling slips past the
  rule. `project ios build --allow-untrusted` builds a pull request whose head
  is not trusted, which runs that head's build scripts as you, so
  `Bash(project ios *allow-untrusted*)` asks before it, and every
  `project ios` command refuses an abbreviated option.
  `project inbox answer`, `dismiss`, `snooze`, `done`, `decline`,
  `start`, and `unclear` record your answer to an item an agent posted, your
  outcome for a request, your consent to a request's start action, your word
  that you don't understand an item, or your choice to dismiss or put it
  off, so an agent running one could answer its own question, mark its own
  request done, consent to its own start action, or mark its own item
  unclear. `discuss` and `read` write in an item's thread as you and record
  that you read the agent's replies, and `project console` records all of
  these from a terminal, so they ask too.
  Projector's panel runs them itself, not through an agent's shell, so
  answering from the panel never asks. Claude Code checks ask rules before
  allow rules, so an ask rule prompts for a command that `Bash(project *)`
  also allows, and a trailing ` *` matches the command with no arguments
  too. `project` refuses an option before the command, so every command line
  it runs starts with its command, and that command's ask rule matches
  wherever the options go: `Bash(project init *)` asks before
  `project init --root .`. A line such as `project --root . init` matches no
  ask rule, and `project` refuses it.

  `project edit`, and `project create` without `--no-edit`, open your
  `$VISUAL` or `$EDITOR`, and need no ask rule. They open it only at a
  terminal, which Claude Code gives no command, and Claude Code lets an allow
  rule match past a leading variable assignment only for a fixed list of
  variables, such as `LANG`, that holds neither of those, nor `PATH` or
  `HOME`. A command such as `VISUAL='sh -c …' project edit plan`, which picks
  the program Projector runs, therefore falls to Claude Code's usual
  approval.

  `project review gate` runs the checkout's `review.gate` command, and stays
  allowed so a reviewing session runs unattended. An agent that edits
  `review.gate` and then runs the gate therefore runs that command without a
  prompt, the same edit-then-run pattern as any allowed test runner. Codex
  leaves the gate to its usual approval, as the next entry describes. The ask
  rules match the command text an agent writes, so they keep an agent from
  running these commands unasked, not from spelling one another way.

  When `CLAUDE_CONFIG_DIR` is set, the file is `settings.json` in that
  directory instead. Without the allow rule, Claude Code asks before each
  Projector command an agent runs, which pushes the agent toward editing
  Projector's files by hand or calling `gh api` itself. Its auto mode can
  refuse a reviewing session's clean review of your own pull request as
  self-approval, although the review is a `COMMENT` that moves no
  `reviewDecision` and a person still merges. It can refuse the deletion of a
  finished review's start comment as a write to an external system, which
  leaves the pull request saying a review is in progress. The reply and
  resolution that settle a finding
  thread are writes of the same kind. It can also refuse the publish of a
  summary you asked for. An allow rule resolves the command before the
  classifier runs. The rules go in your settings rather
  than the repository's, so they hold in every repository, clone, and worktree
  you open, whether or not the repository commits or ignores `.claude/`, and
  no repository has to carry a permission for everyone who opens it. `init`
  never touches the repository's own `.claude/settings.json`. Because the
  rules exempt commands from the classifier, `init` adds them only when you
  run `init` at a terminal, or when you pass `--publish-rule`. Run without a
  terminal, as an agent refreshing the instructions runs it, `init` reports
  the file as `kept` and says on stderr how to add the rules, and
  `review.publish_rule = true` does not change that. Pass `--no-publish-rule`
  to leave the file alone for one run. To check the rules without `init`, run
  `project review rules`, which writes nothing. `init` adds the rules to the
  file's existing settings, removes from `permissions.allow` the narrower
  rules `Bash(project *)` replaces, such as `Bash(project review publish *)`,
  removes `Bash(project -*)` from `permissions.ask`, because `project`
  refuses every line that rule asks before, keeps every other setting,
  writes through a symlink such as one into a dotfiles repository, and
  writes the file back as two-space JSON. It keeps a file that is not valid
  UTF-8 JSON, holds settings of an unexpected shape, sits under a `.claude`
  that is not a directory, or cannot be written, as one linked into a
  read-only directory such as the Nix store cannot, and says on stderr how to
  add the rules yourself. `init` still exits 0.
- `~/.codex/rules/projector.rules`, a Codex rules file, allows the same
  commands for Codex, whose approval reviewer otherwise can refuse
  `project summary publish` as sending repository content to another host.
  When `CODEX_HOME` is set, the file is `rules/projector.rules` in that
  directory instead. `init` writes it only where that directory exists, so it
  never creates a Codex configuration for you. The file is Projector's own:
  Codex loads every `.rules` file in the directory and saves the approvals you
  grant to `default.rules`, so `init` rewrites `projector.rules` whole and
  leaves your other rules alone. The terminal, `--publish-rule`, and
  `review.publish_rule` decide it exactly as they decide the Claude Code
  rules.

  A Codex rule matches exact words with no wildcard, and the file allows
  commands by name. Codex runs a command that an `allow` rule matches without
  asking, and outside its sandbox, so a publish reaches GitHub and the site.
  Codex can also give a command a terminal that the agent types into, so the
  file leaves out every command that can run another program, as well as
  those that reach outside Projector's own state:

  ```text
  prefix_rule(
      pattern = ["project", ["check", "done", "help", "list", "loop", "owner", "priority", "search", "show", "status", "summary", "version", "watch", "worktree"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "agent", ["list", "read", "register", "reply", "send", "unregister"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "config", ["get", "list", "paths"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "inbox", ["ask", "block", "claim", "delivered", "list", "ready", "reopen", "reply", "request", "restore", "revise", "show", "tell", "watch", "withdraw"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "ios", ["cleanup", "crashes", "devices", "install"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "review", ["census", "finish", "hold", "interdiff", "move", "prune", "publish", "ready", "release", "rules", "settle", "setup", "status", "suggestions", "workflow"]],
      decision = "allow",
  )
  prefix_rule(
      pattern = ["project", "site", ["page", "status", "workflow"]],
      decision = "allow",
  )
  host_executable(name = "project", paths = [])
  ```

  The last line keeps the rules to the bare name `project`. Codex matches an
  absolute program path, such as `/tmp/x/project`, against the rules for its
  basename unless a `host_executable` entry lists the paths it accepts. This
  one lists none, so `/tmp/x/project summary publish` goes to Codex's usual
  approval instead of running outside the sandbox, while the bare
  `project summary publish` an agent runs stays allowed. Claude Code matches
  the command text, so `Bash(project *)` never matches a path to a program.
  Codex 0.107 is the first to parse `host_executable`, and an older Codex
  that meets a rules file it cannot parse loads no rules at all, your own
  included. So `init` runs `codex --version` first. When the `codex` on
  your `PATH` reports a version older than 0.107, `init` writes the rules
  without the `host_executable` line and its comment, and says on stderr
  to update Codex and run `init` again. Such a Codex resolves no program
  path, so the line closes no hole for it. Every Codex that uses
  `~/.codex` reads the same rules, though, and while the file lacks the
  line, any Codex 0.108 or newer that reads it, such as the desktop app or
  an IDE extension beside an old `codex` command, runs a path to a program
  named `project` outside its sandbox. So update or remove the old `codex`
  and run `init` again. When `init` finds no `codex`, or
  one that reports no version, it writes the whole file, so update a Codex
  that is not on your `PATH`, such as one only the desktop app runs, to
  0.107 or newer. `project review rules` reads the version the same way,
  and reports a file that holds the line for a Codex too old to parse it.

  No rule matches `project init`, `project upgrade`, `project config set` or
  `unset`, `project create` or `project edit`, whose editor an agent could
  drive, `project review gate`, `project site build` or `serve`,
  `project ios build` or `logs`, which run the repository's build scripts or
  `sudo`, `project inbox answer`, `dismiss`, `snooze`, `done`, `decline`, `start`,
  `unclear`, `discuss`, or `read`, which record your choice, words, or reading,
  `project console`, whose terminal an agent could type answers into, or a
  command line that starts with an option, so Codex's usual approval
  decides them, in its sandbox. A command added to Projector later reaches
  Codex once Projector names it and you run `init` again. To see what the
  rules allow, run
  `codex execpolicy check --resolve-host-executables --rules ~/.codex/rules/projector.rules project summary publish`,
  where `--resolve-host-executables` resolves a program path as a live
  session does. That checks the command alone. Codex checks each command of a chain such as
  `gh repo view && project summary publish` on its own and takes the
  strictest result, so a command no rule names sends the whole call to its
  usual approval, and an agent runs each `project` command as a call of its
  own. Codex reads its rules only when a session starts, so start a new
  session after `init` writes the file.
  `init` keeps the file, and says on stderr how to add the rules yourself, when
  the file is not valid UTF-8, cannot be read, or cannot be written, as one in
  a read-only directory cannot, or when `rules` is not a directory. `init`
  still exits 0.

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
JSON document in `--json` mode. Your Claude Code settings and Codex rules
files are kept for the reasons their entries above name, and `init` still
exits 0. Each is reported with your home directory as `~`, in `--json` mode
too, or by its full path when `CLAUDE_CONFIG_DIR` or `CODEX_HOME` points
outside your home directory.

The two rules files belong to you, not to a repository, so `init` sets them up
outside a Git repository too. Run there, it writes only those files, reports
them as it does in a repository, says on stderr that it adopted nothing else,
and exits 0. Its `--json` document then lists `files` with no top-level
`action` or `path`, because there is no projects README. `--site` and `--url`
need a repository, so outside one they exit 2.

To keep Projector out of your instruction files, set `instructions.enabled`
to `false` in the repository's `.projector.toml`:

```sh
project config set instructions.enabled false
```

With that key false, `init` manages only the projects README and your
permission rules, and `check` says nothing about `AGENTS.md` or `CLAUDE.md`.
To leave your Claude Code settings and Codex rules alone when `init` runs in
this repository, run:

```sh
project config set review.publish_rule false
```

The Projector section carries rules for building an iOS app with
[`project ios`](#put-an-ios-app-on-a-device) only in a repository that has
one. `init` and `check` look for an Xcode project that Git tracks, or that is
untracked and not ignored, so a new app counts before its first commit and a
dependency's project under an ignored folder does not. The project counts
only when its `project.pbxproj` names the iOS SDK, `iphoneos`, as
`SDKROOT = iphoneos` or a multiplatform target's `SUPPORTED_PLATFORMS` does,
because `project ios` builds only for iOS. A Mac-only app, and a workspace
with no iOS project in the repository, get no rules. A repository whose
`ios.project` names its project gets the rules wherever that project lives.
To choose for yourself, set `instructions.ios`:

```sh
project config set instructions.ios false
```

When an app arrives or leaves and the section no longer matches, `check`
warns with `instructions-ios`, and `init` adds or removes the rules.

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

Pass `--url` to serve the site at a custom domain instead of the address
GitHub assigns. `init` sets the domain on the Pages site, after making a
private repository's site private, reports the site at that address, and
points the repository's website link at it, replacing a link to anywhere
else. The value is a domain's address, with no path or port and a host of
letters, digits, hyphens, and dots, and a value without a scheme means
`https://`:

```sh
project init --url projects.example.com
```

GitHub serves the site there once the domain's DNS points at GitHub Pages;
see [Managing a custom domain for your GitHub Pages site](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).
`--url` with `--no-site`, or with `site.enabled = false`, is a usage error.

Changing Pages or the website link takes admin rights on the repository.
Without them, `init` changes nothing on GitHub and prints the `gh` commands
an admin runs to make those changes: turning Pages on or switching it to
GitHub Actions, making a private repository's site private, setting the
domain `--url` names, and linking the website to the site. It still writes
the workflow, because proposing the workflow needs no admin. Hand the
commands to an admin and merge the workflow through a pull request, in
either order; the site deploys once both are done:

```console
$ project init
project: site not set up: you are not an admin of owner/example, so init cannot turn on its GitHub Pages site; pass --no-site, or set site.enabled = false, to stop setting it up
project: to finish setting it up, ask an admin to run these commands; the site deploys once they have and the site workflow is on the default branch:
    gh api -X POST repos/owner/example/pages -f build_type=workflow
    gh api -X PATCH repos/owner/example -f homepage="$(gh api repos/owner/example/pages --jq '.html_url | rtrimstr("/")')"
project: commit .github/workflows/projector-site.yml to the default branch through a pull request; GitHub runs the site workflow only from there
unchanged docs/projects/README.md
unchanged AGENTS.md
unchanged CLAUDE.md
unchanged ~/.claude/settings.json
created .github/workflows/projector-site.yml
```

A workflow that reaches the default branch first cannot publish too early.
Its run fails at `actions/configure-pages` until the Pages site exists, and
its build refuses to deploy a private repository's site while that site is
public. Once the admin is done, start it with
`gh workflow run projector-site.yml`, or rerun the failed run from the
repository's Actions tab.

When `gh` is missing, the repository already deploys its own site, or GitHub
refuses to make a private repository's site private, as it does without
private Pages (GitHub Enterprise Cloud), `init` writes no workflow. In those
cases, and when you are not an admin and Pages needs changing, `init` adopts
the repository as usual, skips the site with a note on stderr, and exits 0.
If `init` created the Pages site in that same run and cannot make it
private, it deletes the site again, so a
private repository is never left with a public site; it never deletes a
site it did not create. A repository whose `origin` is not on GitHub skips
the site without a note. `project site serve` still serves the site
locally. Pass `--site` to make the other failures an error that exits 65,
or `--no-site` to leave GitHub alone. To skip the site every time, run:

```sh
project config set site.enabled false
```

In `--json` mode the document gains a `site` object with `pages`,
`visibility`, `url`, `public`, and `website`, or with `skipped` and the
reason when the site was not set up. When the site needs an admin, `site`
also has `admin_commands`, the list of commands described above.

### Require a clean review before merge

In a repository that relies on Projector alone to sign off pull requests, a
ruleset can make GitHub block a merge until the current head has a clean
Projector review. `project review publish` posts the `projector/review` status
on each commit it reviews wherever the default branch requires that status. A
new head starts with no status, so it waits for its own review. In a
repository where people approve pull requests too, as at most workplaces,
leave the check off: a clean Projector review is not a merge decision there.

When you run `init` at a terminal in a GitHub repository whose default branch
does not require `projector/review` yet, it asks:

```text
Does this repository rely on Projector alone to sign off pull requests before merge? [y/N]
```

`--require-review` answers yes and `--no-require-review` answers no, without
a prompt, and both are refused outside a Git repository. Without a terminal
or a flag, `init` asks nothing and changes nothing but the review labels, as
[Keep the review labels current](#keep-the-review-labels-current) describes.
When `gh` cannot read the
default branch's rules, because it is missing, signed out, or offline,
`init` skips the question with a note and changes nothing, unless a flag or a
recorded `true` asked for the check, which then fails `init`. A yes does four
things in order:

1. Creates the `Projector review` ruleset, which requires `projector/review`
   on the default branch, with no bypass. Creating a ruleset takes admin
   access, and a private repository needs a paid plan for rulesets. When
   GitHub refuses, `init` says why, exits non-zero, and does nothing else.
2. Writes `.github/workflows/projector-clean.yml`, which takes the `clean`
   label off a pull request when a commit is pushed to it. Its job reads the
   pull request when it runs, not as it was at the push, and keeps the label
   when the current head already has a `success` `projector/review` status. So
   a job that runs late, or a re-run of an old job, never takes `clean` off a
   head that a review signed off after the push. After it removes the label,
   it reads the status once more and gives the label back when a review signed
   the head off in the meantime. When a push moved the head while it gave the
   label back, it takes the label off again. Commit the workflow through a
   pull request; it runs once it reaches the default branch. `init` replaces a
   workflow that an earlier release wrote and keeps one you edited. A kept
   copy never takes a later release's fixes, so `init` says how to compare it
   with Projector's current workflow, which `project review workflow` prints:

   ```sh
   project review workflow | diff -u .github/workflows/projector-clean.yml -
   project review workflow > .github/workflows/projector-clean.yml
   ```

   With `review.labels` set to `false`, `init` writes no workflow, because
   the workflow would take a `clean` label of the repository's own off.
3. Posts the `success` status on the current head of each open pull request
   that a review has already signed off, so none waits on a review it has.
   When GitHub refuses that, `init` says so and goes on, because a rerun finds
   the check required and posts nothing. Review each pull request that should
   merge again, with `project review setup <number> --rereview` and then
   `project review publish`, which posts the status.
4. Records `review.require_check = true` in the repository's
   `.projector.toml`.

A no records `review.require_check = false`, and `init` asks no more. Once
the setting is `true`, `init` creates whichever of the ruleset and the
workflow is missing, without asking. When the default branch already requires
`projector/review`, as a ruleset an admin added by hand does, `init` asks
nothing and writes the workflow. `init` never removes anything. To stop
requiring the check, delete the ruleset in the repository's settings, delete
the workflow, and run:

```sh
project config set review.require_check false
```

### Keep the review labels current

In a GitHub repository, `init` also brings the `reviewing` and `clean`
labels up to date, whatever you answer about the check:

- It creates each label the repository lacks, with the color and
  description that
  [Review a pull request](#review-a-pull-request) lists.
- It gives a label the current color when the label still has a color an
  earlier release created it with and Projector's description, such as the
  yellow `fbca04` and the green `0e8a16` that `reviewing` and `clean` had
  before they took the panel's colors. Nobody chose those colors, so `init`
  moves them. A label with any other color or description is the
  repository's own, and `init` leaves it as it is.

`init` prints a line for each change, such as
`project: recolored the reviewing label from fbca04 to d77757 on acme/app`.
When GitHub refuses, `init` says so and goes on, because the labels only show
a review. With `review.labels` set to `false`, `init` leaves the labels
alone.

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

`create` defaults to `--status draft` and `--priority later`. The new plan
opens with a one-sentence summary to fill in, then numbered sections for the
problem, the solution, the reasons for the design, and the acceptance
criteria, in that order, so the design leads and the evidence follows.

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

An `owner:` line with an empty value, such as `owner:` or `owner: ""`, names
no one, so Projector reads the plan as having no owner: `check` accepts it,
`list --owner` never selects it, `owner` fills in the value on that line, and
`--clear` removes the line.

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
missing priorities, a multi-line owner, missing top-level
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
warning: AGENTS.md: Projector section is version 4; this command ships version 5 (run 'project init' to refresh it; your own content is not changed) [instructions-outdated]
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
| `instructions-ios` | the section carries the iOS rules in a repository that does not need them, or leaves them out of one that does | `project init`, or set `instructions.ios` |
| `instructions-edited` | the section was edited by hand | `project init` to restore it, or change the template in Projector |
| `instructions-malformed` | a begin marker without an end marker, or a second pair | repair the markers by hand |
| `instructions-external` | the file is a symlink to a file outside the repository | replace the link with a file in the repository, or set `instructions.enabled = false` |
| `instructions-unlinked` | the file is a symlink checked out as a plain file holding the link text | enable Developer Mode or run as an administrator, set `core.symlinks=true`, and check the repository out again |

A path that resolves outside the repository reports only
`instructions-external`; `init` never writes there, so it is the one warning
`init` cannot clear.

`check` also warns while the site workflow,
`.github/workflows/projector-site.yml`, is an earlier shape that Projector
wrote. That workflow does not deploy when a pull request closes, as
[Build the Projector site](#build-the-projector-site) describes. A workflow
edited by hand gets no warning.

```console
$ project check
warning: .github/workflows/projector-site.yml: the site workflow is an earlier shape and does not deploy when a pull request closes (run 'project init' or 'project site workflow --write' to refresh it) [site-workflow-outdated]
Project plans are valid.
```

| Code | Condition | Fix |
| --- | --- | --- |
| `site-workflow-outdated` | the site workflow is an earlier shape Projector wrote | `project init`, or `project site workflow --write` |

`check` also warns while the repository's shared `<app> Development`
schemes build more than one app and `ios.app` names none of them. Once two of
those apps have kept builds, `project ios crashes` and `logs` cannot tell
which app you mean and refuse to choose, as
[Find the app by convention](#find-the-app-by-convention) describes. The
schemes are read from the files Git tracks, or that are untracked and not
ignored, so `check` needs no Xcode.

```console
$ project check
warning: .projector.toml: Development schemes build Alpha, Beta, and ios.app names none of them, so `project ios crashes` and `logs` refuse to choose once two have kept builds (run 'project config set ios.app <app>' with the app to build by default) [ios-app-unset]
Project plans are valid.
```

| Code | Condition | Fix |
| --- | --- | --- |
| `ios-app-unset` | shared `<app> Development` schemes build several apps, and `ios.app` is unset | `project config set ios.app <app>` |

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
summarize = false
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

Together those resolve `review.summarize` to `false` and `review.model` to
`fable`. Arrays replace rather than append.

Read one value with a dotted key, which reaches into a table:

```sh
project config get review.summarize
project config get review.summarize --default true
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
project config get review.summarize --json
```

```json
{
  "key": "review.summarize",
  "schema_version": 2,
  "source": "/Users/you/ninjudd/.projector.toml",
  "value": false
}
```

`get` reads any key, so a skill or a script can keep settings of its own in
these files. [`set`](#change-configuration) accepts only the keys Projector
reads, which are these:

| Key | Type | Default | Read by |
| --- | --- | --- | --- |
| `projects.dir` | string | `docs/projects` | every command, unless `--projects-dir` is given |
| `instructions.enabled` | boolean | `true` | `init` and `check`, to manage the Projector section in `AGENTS.md` and `CLAUDE.md` |
| `instructions.ios` | boolean | whether the repository has an Xcode project that builds for iOS, or sets `ios.project` | `init` and `check`, to put the rules for `project ios` in the Projector section |
| `site.enabled` | boolean | `true` | `init`, to set up the GitHub Pages site and its workflow unless `--site` or `--no-site` says otherwise |
| `site.prepare` | string | none | `site build` and `site serve`, as a shell command to run in the checkout before building, once allowed with `--allow-prepare`, unless `--prepare` or `--no-prepare` says otherwise |
| `review.username` | string | the authenticated user | `review-pr` and `review-repo`, as the GitHub login that posts reviews, and `project watch add --role review`, as the login a review subscription names |
| `review.allow_approve` | boolean | `false` | `review-pr` and `project review publish`, to permit a real `APPROVE` on a clean cross-author review |
| `review.publish_rule` | boolean | `true` | `init`, to add the rules allowing every `project` command but the few that reach outside Projector's own state to your Claude Code settings and Codex rules |
| `review.gate` | string | none | `project review gate`, as the repository's validation gate: a shell command run in the review's scratch worktree with the worktree and the merge base as `$1` and `$2`. With `review.gate_checks` set, it names only the part of the gate CI does not run |
| `review.gate_checks` | string | none | `project review gate`, which waits for these GitHub Actions checks on the head instead of running the suite, and `implement` and `fix-pr`, which run focused tests before a push instead of the full suite: the names of the CI checks that run the repository's suite, separated by commas, as `check` or `test, lint` |
| `review.incremental` | boolean | `true` | `project review interdiff` and `project review publish --incremental`, to allow an incremental review of a minimal push; `false` gives every head a full review |
| `review.summarize` | boolean | `true` | `review-pr`, to publish a summary of each pull request it reviews to the repository's Projector site after the review |
| `review.labels` | boolean | `true` | `project review setup`, `publish`, and `finish`, to add and remove the `reviewing` label, which shows a review in progress, and the `clean` label, which shows a signed-off head; `false` leaves both labels alone, as a repository whose own `clean` or `reviewing` label means something else needs |
| `review.require_check` | boolean | none | `init`, as your answer to whether the repository relies on Projector alone to sign off pull requests: none asks at a terminal, `true` keeps the ruleset and the label workflow in place, and `false` asks no more; `init` writes it when you answer, as [Require a clean review before merge](#require-a-clean-review-before-merge) describes |
| `fix.resolve_human_threads` | boolean | `true` | `fix-pr`, to resolve a person's review thread once its fix is pushed; `false` replies and leaves resolving to the reviewer |
| `watch.auto_start` | boolean | `true` | the skills and `project watch add --from-output`, to subscribe the session to fix each pull request it opens or pushes to; `false` adds no such subscription, and `project watch add` still adds one you ask for |
| `watch.interval` | whole number | `60` | the watch daemon, from `~/.projector.toml` only, as the seconds between passes; at least `30` |
| `watch.full_every` | whole number | `600` | the watch daemon, from `~/.projector.toml` only, as the most seconds a pull request goes without a GraphQL query |
| `watch.renotify` | whole number | `900` | the watch daemon, from `~/.projector.toml` only, as the seconds before work that still stands is announced again |
| `watch.review_stall` | whole number | `1800` | the watch daemon, from `~/.projector.toml` only, as the seconds a review's lock can go unchanged before the daemon treats the review as stalled |
| `panel.background` | string | `auto` | Projector's panel in Claude Code, as its background in a terminal: `auto` picks one from the Claude Code theme; `rgb(r,g,b)`, `#rgb`, `#rrggbb`, or `ansi256(n)`, with each number from 0 to 255, is drawn as written; `sidebar` draws the theme's `composerSidebarBackground`, `black` `rgb(0,0,0)`, and `white` `rgb(255,255,255)`. The panel draws any other value as `auto` and says so once |
| `agent.message_limit` | whole number | `12` | `project agent send` and `reply`, from the checkout the sending session works in, as the most messages one session sends to one other session in a rolling hour; at least `1` |
| `agent.auto_register` | boolean | `true` | `project watch add` and the Codex hooks, from the checkout the session works in, to register a session that subscribes to a pull request for agent messages, with a generated summary; `false` subscribes it without registering it, as [Registered when it subscribes](#registered-when-it-subscribes) describes. A session that ran `project agent unregister` is never registered this way, whatever the setting says |
| `agent.key` | string | none | the relay, from `~/.projector.toml` only, as `keychain:<key-id>`, naming the private key in the OS keychain, or, with `--inline`, the key itself, `x25519:` and a base64 private key. Its presence turns the relay on. Set it with `project agent key init` or `key join`, never `config set`; see [Message agents on other machines](#message-agents-on-other-machines) |
| `ios.app` | string | the Xcode project's name | `project ios`, as the app whose scheme is `<app> Development` or `<app>`, unless `--app` names another |
| `ios.project` | string | the one `.xcworkspace`, or the one `.xcodeproj` | `project ios build`, as the path of the Xcode project or workspace, relative to the repository's root |
| `ios.output` | string | `~/Desktop` | `project ios crashes` and `logs`, as the folder they write crash reports and log files to |

`review.allow_approve` is off unless it is exactly `true`; an unset key means
`false` rather than a question to ask. It never applies to a review of your own
pull request, where GitHub refuses the verdict regardless. `review.incremental`
is on unless it is `false`, and a value that is neither `true` nor `false`
makes both commands that read it refuse and name the key. `review.labels` is
on unless it is exactly `false`. `setup` and `publish` read it from the
review's checkout, and `finish` from the checkout it runs in.

The operator -- the account whose branches carry fixes and whose token pushes
them -- is deliberately not a key. It follows whichever token is authenticated,
because the skills and the watch daemon act through that token and a file
naming a different account could only disagree with it. Which pull requests a
session watches is not an identity question either: the session's
[subscriptions](#watch-pull-requests) name them, and the daemon sees nothing
outside them.

One daemon serves every repository on the machine, so it reads
`watch.interval`, `watch.full_every`, `watch.renotify`, and
`watch.review_stall` from `~/.projector.toml` alone and ignores them in a
repository's file. Set them with `--user`. The daemon reads a hand-written
`watch.interval` below 30 as 30.

## Change configuration

Change one setting with `set`, and remove one with `unset`:

```sh
project config set watch.auto_start false
project config unset watch.auto_start
```

Both change the repository's own `.projector.toml` at the root of the
checkout, which is the file nearest the code and the last one merged, so the
value you set is the value the repository's commands read. Choose another
file with one of these options:

- **`--user`** changes `~/.projector.toml`, which applies in every
  repository. Use it for a personal preference, such as `watch.auto_start`,
  that should not be checked in with a repository.
- **`--file <path>`** changes the file at that path, such as
  `~/work/.projector.toml`, which covers every repository under `~/work`.
  The command creates the file but not its directory, and refuses a path
  in a directory that does not exist with exit code 2.
- **`--repo`** names the default.

Outside a Git repository, the default has no file to change, so `set` and
`unset` exit 69 unless you pass `--user` or `--file`.

`set` takes `true` or `false` for a boolean key, a whole number for a
whole-number key, and any text for a string key. It refuses any other value
with exit code 2, as it does a whole number below 30 for `watch.interval` or
below 1 for the other `watch` numbers and `agent.message_limit`. It also refuses, with
exit code 2 and the list of keys, a key that the table in
[Read configuration](#read-configuration) does not list. `get` still reads
a key that is not in the table.

`unset` removes any key the file sets, including one the table does not
list, such as a setting Projector no longer reads:

```sh
project config unset review.peer_wait
```

A key the table lists but the file does not set is reported as
`unchanged`. A key outside the table that the file does not set, such as a
misspelled one, is refused with exit code 2 and the list of keys.

`set` and `unset` edit the file as text and change only the line that sets
the key, so your comments, blank lines, and other settings stay as you wrote
them:

- **A key that is set** gets its new value on the same line, with any
  comment after the value kept.
- **A key that is not set** goes after the last setting of its table, such
  as `[watch]`. When the file sets the table's keys with dotted names, such
  as `watch.auto_start = true` at the top of the file, the new key is
  written the same way, beside them.
- **A table the file lacks** is added at the end of the file, and a missing
  file is created.
- **`unset`** removes the key's line and leaves its table header. It
  removes one setting, not a table, so it refuses a key that names a
  table, such as `review`, with exit code 65 and the file unchanged.

Each command refuses a file that is not valid TOML with exit code 78, and a
key it cannot change on its own line, such as one in an inline table
(`watch = { auto_start = true }`), with exit code 65. Neither refusal
changes the file. After each change, the command reads the file back and
checks that the key has the new value and that every other key is
unchanged. When the check fails, it restores the file and exits 65. A
symlink, such as a `~/.projector.toml` that links into a dotfiles
repository, stays a link, and the command changes the file it points at.

A nearer file can override the value you set, as a repository's file
overrides `~/.projector.toml`. When the merged configuration does not give
the new value, the command names the file that wins on stderr:

```console
$ project config set watch.auto_start false --user
set watch.auto_start = false in /Users/you/.projector.toml
project: /Users/you/work/repo/.projector.toml sets watch.auto_start = true, which applies here
```

Setting a key to the value it already has changes nothing, and the command
reports it as `unchanged`. With `--json`, each command prints the `action`,
which is `set`, `unset`, or `unchanged`. It also prints the `key`, the
`old` value and the `new` value, each `null` when the file has none, and
the `file` it changed:

```json
{
  "action": "set",
  "file": "/Users/you/work/repo/.projector.toml",
  "key": "watch.auto_start",
  "new": false,
  "old": true,
  "schema_version": 2
}
```

You can still edit a `.projector.toml` by hand. The commands write the same
TOML, such as this for `project config set watch.auto_start false`:

```toml
[watch]
auto_start = false
```

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
  bash`, from a release's source archive, or from a Git URL: `upgrade` asks
  GitHub for the newest release's tag, such as `v0.9.0`, downloads
  `install.sh` at that tag from `raw.githubusercontent.com`, and runs it with
  the same targets and `PROJECTOR_REF` set to the tag. The installer and the
  CLI it installs then come from one release, and a tag never moves, so no
  cache can serve an older copy of either. `upgrade` asks with
  `gh api repos/<owner>/<repo>/releases/latest` when `gh` is signed in
  (`PROJECTOR_GH_COMMAND` names another `gh`), and with GitHub's anonymous
  API otherwise. It prints the equivalent
  `curl ... | PROJECTOR_REF=<tag> bash -s -- <targets>` command on stderr.
- **A checkout**: `pipx` installs a copy of the source, so a checkout that
  moves on leaves the installed command and plugins behind. `upgrade` runs
  the checkout's `install.sh`, prints that command on stderr, and ends with a
  row saying whether the checkout is behind its upstream; a `repo-behind` row
  means the command just built is older than main, so pull and run `upgrade`
  again.

A release install upgrades from the repository it was installed from, so a
command installed from a GitHub fork upgrades from the fork's releases, with
`PROJECTOR_REPO` set to the fork. These variables change what it installs:

- **`PROJECTOR_REPO`** names another repository, such as `someone/projector`.
- **`PROJECTOR_REF`** names the release to install, such as `v0.5.7`, and
  skips the lookup.
- **`PROJECTOR_INSTALLER_URL`** names another installer to run.

When GitHub cannot name the newest release, as without a network, under the
anonymous API's rate limit, or in a repository with no release yet,
`upgrade` prints one warning on stderr that says why, and runs the installer
that `v0` serves instead: `https://projector.bot/install.sh`, or `install.sh`
at `v0` in a fork. projector.bot caches that installer for a few minutes, so
just after a release it can still serve the one before.

`upgrade` exits with the installer's status, and targets are the
installer's to validate: an unknown one is its usage error, exit 64. See
[the plugin guide](plugins.md) for what each target does. A release install
that succeeds but does not match its release exits 1, as
[Check the install against the release](#check-the-install-against-the-release)
describes. `upgrade` exits 69
when the command is not an installed distribution, when the record of its
source is missing, when a checkout or its `install.sh` no longer exists, or
when the released installer cannot be downloaded.

`upgrade` has no `--json` mode because the installer owns the output.

### Check the install against the release

After a release install succeeds, `upgrade` runs the same installer's
`status` and checks what it reports for each target it installed:

- **The CLI.** The version of the `project` on your `PATH`, which is the
  command the install left rather than the one running the upgrade.
- **Claude Code.** The `projector` plugin.
- **Codex.** The `projector` and `projector-hooks` plugins.

A host that is not installed is not checked. When everything matches,
`upgrade` prints one row:

```text
verified       what this upgrade installed matches the v0.9.0 release
```

Otherwise it prints each row that names a problem, with the command that
fixes it, then a `mismatch` row, and exits 1:

```text
cli-stale      ⚠️  installed 0.8.18, release 0.9.0 -- run project upgrade cli
hooks-absent   ⚠️  codex projector-hooks@projector -- run project upgrade codex
mismatch       ⚠️  what this upgrade installed does not match the v0.9.0 release -- run the command each row above names
```

A `plugin-ahead` row means that a host installed a plugin newer than the
release. Each host's marketplace follows the default branch, which carries a
new version a few minutes before its release is published, so run
`project upgrade` again once the release is out. When `status` has no release
version to compare with, `upgrade` prints an `unverified` row instead and
exits 0.

Trusting Projector's hooks in Codex's `/hooks` is a step only you can take,
so the check does not look at it. A checkout install is not checked, because
its `status` compares the install with the checkout rather than a release.

### Know when to run `init` again

After the installer's rows, `upgrade` prints rows of its own, in the same
format, that say whether you need to run `project init` again. A release can
change four things `init` writes: the Projector section in `AGENTS.md` and
`CLAUDE.md`, the site workflow, the clean label workflow, and the permission
rules in your Claude Code settings and Codex rules. `upgrade` compares what
the command it replaced writes with what the `project` on your `PATH` writes
after the install, which is the command you run `init` with next, and lists
each one that changed:

```text
init-changed   the Projector section in AGENTS.md and CLAUDE.md
init-changed   the permission rules in your Claude Code settings and Codex rules
init-needed    ⚠️  run project init in each repository that uses Projector to write the changes above
```

`init-needed` carries a ⚠️ marker and prints in bold yellow on a terminal,
unless `NO_COLOR` is set. When nothing changed, `upgrade` prints one row
instead:

```text
init-current   nothing project init writes changed
```

`init-unknown` means the `project` on your `PATH` cannot say what it writes,
as a release older than this check cannot, or that no `project` is on your
`PATH`. Run `project check` in each repository then; it warns about an
outdated Projector section or site workflow. An install that fails can still
have replaced the command, so after a failure `upgrade` lists what changed but
prints no `init-current` or `init-unknown` row. A target that leaves the CLI
alone, such as `claude` or `status`, changes nothing `init` writes.

The rows compare two installs, not your repositories. A repository where you
skipped `init` after an earlier upgrade can still be behind when this one
changed nothing, and `project check` says so there.

### Know when a release is out

A command installed from a release warns on stderr when a newer release is
out. Every Projector skill runs `project`, so an agent using a skill sees the
warning too:

```text
warning: project 0.6.4 is behind the 0.6.6 release; run 'project upgrade' to update the CLI and the Projector plugins
```

The warning also follows a usage error, because a usage error is what a newer
skill meets when it calls a subcommand or flag an older command lacks. It
never changes stdout or the exit status, and `help`, `version`, and
`upgrade` itself do not print it.

The command reads the release's version from the plugin manifest at
`PROJECTOR_REF` (default `v0`, which a release moves last) in the repository
`upgrade` installs from. It checks at most once a day, waits at
most two seconds for an answer, and caches the answer in
`$XDG_CACHE_HOME/projector/release.json` (`~/.cache/projector/release.json`
by default). A check that fails also waits a day before the next one. Where
that cache cannot be written, as in a read-only home directory, the command
skips the check rather than fetching on every run.
`PROJECTOR_OFFLINE=1` skips the check and warns only from the cache. To stay
on a pinned release without the warning, set `PROJECTOR_REF` to its tag, such
as `v0.6.2`. A command installed from a checkout does not check, because
`install.sh status` compares it with the checkout instead.

## Build the Projector site

A repository that sets up the Projector site serves it from GitHub Pages,
built from content Projector keeps in the repository: its `README.md`, the
Markdown documents under `docs/`, the project plans, and the pull request
summaries on the hidden ref `refs/projector/summaries`. The
`summarize-pr` skill writes a summary, and these commands do everything
else:

```sh
project summary init --repo OWNER/NAME --pr 66 --summary summary.json
project summary init --repo OWNER/NAME --pr 66 --summary summary.json --from-published
project summary refresh --summary summary.json
project summary publish --summary summary.json
project site page --summary summary.json --out site
project site build --out _site --summaries summaries-ref/summaries
project site serve --port 8000
project site status --repo OWNER/NAME --pr 66
project site workflow --write
```

`summary init` writes a skeleton summary with every changed file in one
unassigned group, an empty `name`, and a `project` of `null`, which means
not decided yet. `summary publish` refuses a summary whose `project` is
`null`, missing, or not a string, or whose `name` is empty or longer than 60
characters, and says which field to set. Set `project` to the name of the
one project the pull request carries out, as `project list` names it, or to
`""` for none, and `name` to a short title for the stack. A project the
checkout does not have yet publishes, because a pull request can add the
plan it carries out. `site page` does not check either field.

`summary init --from-published` starts from the pull request's newest
published summary instead of a skeleton, so a summary carries forward from
one head to the next. It fetches `refs/projector/summaries` from `--remote`,
`origin` by default, and copies the summary the site shows as the pull
request's newest. That is the summary published last, dated by the
`spec.json` an older release stored beside it when there is one, so a
republished old head never outranks a newer head. A summary this checkout
keeps in its own `refs/projector/summaries` counts too, and wins when it is
newer, so a summary previewed with `--local` carries forward. When the
fetch fails, it reads the copy of the remote's ref that this checkout
already has. It reads a head stored only as `spec.json` from that file, and
reads `refs/projector/walkthroughs` while neither the remote nor this
checkout has `refs/projector/summaries`, as `site serve` does. `--head`
copies the summary of the head whose SHA starts with the value you pass
instead. It refuses a `--remote` whose GitHub repository is not `--repo`,
and fails when no summary of the pull request is stored. `--checkout`
names the checkout whose summaries ref it reads, so an agent whose shell is
elsewhere needs no `cd`, as with `summary publish`.

`summary refresh` brings a summary up to its pull request's current head
and base. It sets every `pr` field to what GitHub reports now. That
includes `pr.base`, the merge base the page's diff starts from, which moves
when you merge the base branch into the pull request, and `pr.basePr`,
which it removes once the pull request is no longer stacked. It puts each
file new to the diff in the `unassigned` group, adding that group last when
the summary has none, and drops each file that left the diff, printing that
file's note and checks so you can move what still applies. It lists each
note whose `line` the new diff does not show, leaves that note as it is,
and changes no other prose. It replaces the file in one step, and only when
something changed:

```console
$ project summary refresh --summary summary.json
refreshed summary.json to owner/example#66 at 9c1d07e2a
  pr.head: 4f2a9c107 → 9c1d07e2a
  pr.base: 0b7e3d4c8 → 61aa2f05e
added 1 file new to the diff to the unassigned group; move each into the group it belongs to:
  +40    -0     src/check/name.go (new)
1 note names a line the diff no longer shows; move each to where its code now sits, or remove it:
  src/check/card.go line 88 (new side), in core: flag: The retry ignores the timeout.
```

Pass `--diff` to `summary refresh` when GitHub cannot serve the diff. It
must run from the pull request's current merge base to its current head.

`summary publish` then fetches the pull request's diff, checks
that the summary builds against it, commits the summary and the diff to the
hidden ref without touching the checkout, and starts the repository's site
workflow. It commits the head's `linguist-generated` attributes beside them
as `attributes.json`, as [Generated files](#generated-files) describes. It
also commits, under `head/` beside them, the head's version of each changed
file whose diff hides some of its lines: a file that is neither new nor
deleted, whose diff has a hunk, and whose entry in the head is a regular file
of at most 1 MiB that is not a `.gitattributes` file. Each is a tree entry
naming the blob the head already has, so the ref gains no file content.
Publishes that run at the same time, as a reviewing session's subagents run
them, each land. A publish whose push loses the race for the ref fetches the
ref again, rebuilds its commit on the new tip, keeping every other publisher's
summaries, and pushes again, up to five pushes in all. It says how many
times it retried. Any other push failure, such as a refused login, fails at
once.
Pass `--diff` to publish a diff you produced instead of fetching one; it must
run from the summary's `pr.base` to its `pr.head`, because every later deploy
serves the stored diff as it is. Pass `--no-dispatch` to skip the workflow.
Pass `--checkout <path>` to publish from that checkout rather than the
repository containing the current directory, so an agent whose shell is
elsewhere runs the publish as one command, with no `cd` before it.
`--summary` and `--diff` still name files relative to the current directory.

`summary publish` pushes only to a repository that hosts its site, which
`site status` reports as hosted. Anywhere else nothing on GitHub reads the
ref, so it commits the summary and the diff to this checkout's own
`refs/projector/summaries` and pushes nothing, starts no workflow, and
comments nothing. `site serve` then previews the summary before the site is
set up on GitHub Pages:

```console
$ project summary publish --summary summary.json
committed 3f2a91c07 to refs/projector/summaries in this checkout: summaries/66/…/summary.json and diff.patch; nothing was pushed, because owner/example has no .github/workflows/projector-site.yml on its default branch; preview it with `project site serve`
```

Pass `--local` to keep a summary local even where the site is hosted, to
preview it before it goes live. A summary kept local stays in that checkout.
Once the site is hosted, publish the summary again to push it.

When `summary publish` pushes to a hosted site, it then comments a link to
the summary on the pull request, `📽️ **Projector summary** of <head>: <url>`.
It keeps one such comment per account and pull request, as the pull
request's last message: each publish posts the link again at the end, and
then deletes the account's older summary comments. A rerun on the same head
posts nothing while its comment is still the last item on the pull request's
timeline. Anything the conversation shows after the comment, such as another
comment, a submitted review, a push, a label change, or a ready-for-review
event, makes the rerun post the link again. A pending review, a
subscription, or a mention does not, because readers of the conversation do
not see it.
`summary publish` also puts the same link at the very bottom of the pull
request's description, after a blank line, and removes the link an earlier
publish left there. Where the account cannot edit the description, it prints
why and leaves the description as it was. Pass `--no-describe` to post
the comment and leave the description alone, for a repository whose
instructions forbid agents to edit descriptions.
It sends two `repository_dispatch` events, `projector-summaries` and
`projector-walkthroughs`, because a site workflow written before summaries
were renamed listens only for the second; a later release stops sending it.

A repository that published before the rename keeps its summaries on
`refs/projector/walkthroughs`, under `walkthroughs/`, each stored as
`spec.json`. When the remote has that ref but no `refs/projector/summaries`,
`summary publish` first creates the new ref from it by replaying each of the
old ref's commits with its `walkthroughs/` folder as `summaries/`, keeping the
commit's author, dates, and message, so a summary republished beside one of
those files keeps the date the site orders a pull request's heads by. It
pushes that history with the new summary on top and leaves the old ref in
place; delete it with `git push origin :refs/projector/walkthroughs` once
every site that reads it runs a release that reads the new ref. Until the new
ref exists, `site serve` and the site action read the old one. Either way, the
site reports each summary stored as `spec.json` as skipped until you publish
it again.

`site page` builds one summary into a directory you can open from disk or
publish as a Claude Artifact. It refuses when the pull request has moved past
the summary's head, unless `--at-head` asks for the recorded head; pass `--diff`
when GitHub cannot serve a diff that large.

`site build` builds the whole site from a checkout, this repository unless
`--repo-root` names another, and the summaries under `--summaries`
when there are any. Its menu has three sections. **Projects**, the home page,
groups the projects by status, and opens each one in a summary page's layout,
as the paragraph on project pages below describes. **Reviews** lists the
summaries, newest pull request first, each as
its pull request's number and title, the way a summary's sidebar lists its
stack, with its status and when its newest version was published, in your
time zone. The pull requests of a stack are listed together where the newest
of them would be, from the bottom up, close together with a divider only
below the stack. The build takes each stack from GitHub, which lists the
repository's stacks in one paginated request, so a stack keeps its pull
requests and their order after they merge, and keeps a layer whose base has
moved to the default branch. A pull request in no GitHub stack, and every
pull request when GitHub cannot list the stacks, is stacked when its base
branch is another pull request's head. A summary that did not record the
pull request beneath it has it looked up with `gh` at build time, and when
`gh` cannot answer, that pull request shows no stack. A pull request chained
by its base branch onto a member of a GitHub stack joins that stack, after
its members. The build prints one line saying how it found the stacks, and
never fails over them:

```text
stacks: read 19 GitHub stacks in 1 request
stacks: GitHub's stacks could not be read, so every pull request sits on its base branch
```

Each stack opens with a header, and so does a pull request alone. A summary
records the one project its pull request carries out as `project`, or `""`
for none, and a short title for the stack as `name`. The build splits each
stack into segments, one for each run of pull requests that carry out the
same project, and heads each segment with the project's title, linked to its
page. A pull request that names no project, a project the site has no page
for, or no summary at all sits in the segment of the pull request before it,
or in the first segment when it is the lowest. A stack where none names a
project has one header: the lowest `name` in
the stack, in plain text, or else the title of its lowest pull request with
a summary. A summary published before `project` existed takes the first
project its `projects` list names that the site has, or else the one project
whose files its diff changes, when exactly one does. Two boxes
above the list, **Open** and **Closed**, each with its count, choose which
pull requests it shows. A merged pull request counts as closed, and one whose
state `gh` could not tell counts as open. Only **Open** starts checked, and
the address records any other choice, as `?state=closed`,
`?state=open,closed`, or `?state=` for neither, so a copied link opens the
same list. When the boxes hide every pull request of a segment, its header
goes too. A summary's sidebar lists
every pull request in its stack, the ones beneath it and the ones above it,
under its segments' headers, one row each with its title, and highlights the
current one. A pull request with no summary on the site links to GitHub, and
a pull request with no stack is a stack of one, with its own row highlighted. Its older
versions follow the sections, and as you scroll, the list of sections marks
the one in view. A jump to a section or to the files pane lands it level
with the top of the sidebar. A summary's header shows the same status as its
row on Reviews: **Merged** or **Closed** once the pull request is, and until
then the verdict of the newest Projector review of its head, **Clean** or
**Changes requested**, linked to that review on GitHub. A head no Projector
review names yet shows **Pending** when an earlier head of the pull request
has a verdict, as when the site was built before the head's review landed.
**Pending** links the newest verdict on an earlier head, and its title names
that verdict, as in "Last review: changes requested on e333044. This head
hasn't been reviewed yet." Every other head counts as earlier except those
with newer summaries, so a reviewed head the site has no summary of counts
too. **Unreviewed** means no Projector review names the head or an earlier
one, so on a pull request's newest head it means none names any of its
heads. The newest review of a head decides whichever model wrote it, as it
decides whether `project review publish` signs the head off. When that
review is incremental, the header reads
**Clean · incremental from a8cf3d5**, where the second part names
the head of the full review it builds on, by the same model, and links that
review, while the row on Reviews shows **Clean** alone. Models are told apart
by their model ID, lowercased and without a trailing bracketed variant such as
`[1m]`. In a public repository a review counts only when its author owns the
repository, belongs to its organization, or collaborates on it, because anyone
can review a public repository's pull request. In a private or internal
repository every review counts, because only an account that can see the
repository can review there. That keeps a verdict counting when its author
keeps their organization membership private, which the token the deploy
workflow runs with cannot see. A repository whose visibility GitHub does not
report counts as public. The build reads the pull request's state, its
reviews, and the repository's visibility through `gh` and shows no status
when `gh` cannot answer. **Docs**
renders `README.md`
beside a sidebar of every other document under `docs/`, leaving out the
projects directory, which Projects covers. The Docs sidebar lists its readme
as **Overview** and appears only when there is more than that readme to list,
and the menu button at the start of the header shows and hides it. A repository
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
assets under `assets/`; it defaults to `/`. A project page takes a summary
page's layout. A title row across the top holds the breadcrumbs, the status
and priority badges, the owner when the plan names one, and the plan's
title, so the plan's own first heading leaves the body when the body opens
with it. At the row's end, a **…** menu, as on a review page, holds
**View file on GitHub** and **View history on GitHub**. Beside the plan, the
summary's sidebar opens with the project's header, once. On the project's
readme the header is highlighted and is the page's current item, and on a
supplemental page it links to the readme. Below it, the sidebar lists every
stack that holds a segment the project heads, newest first, each whole:
every layer, merged and closed ones included, and a layer with no summary
on the site as its number, linked to GitHub. Another project's segment
opens with that project's header, linked to its page. The project's own
segments show only their rows, and where one follows another project's
segment, a thin rule above its rows ends that segment. A project that
heads no segment shows **No reviews of this project.** in place of the
stacks. On the readme, the plan's `##` sections
follow, each a link with the plan's own number, such as `2`, in a column
beside its title. Below them, the sidebar lists the project's supplemental
files and nested projects, with the current page in bold, and a
supplemental Markdown page lists its own sections under its entry. A nested
project's sidebar then names its parent project above the parent's other
nested projects, each a link. The sidebar's last row holds the credit and an
icon that opens the page's history on GitHub. As you scroll, the list of
sections marks the one in view. A jump to a heading, from the list or from
a link with its `#` anchor, lands it level with the top of the sidebar, and
the page leaves room after its last section so that one can scroll there
too. An HTML page in a project keeps its frame, with the sidebar in the
column the menu button shows, and lists no sections. Its breadcrumbs above
the frame end in the same **…** menu.
At 980 pixels and narrower, the sidebar sits between the title row and the
plan, and its sections start folded behind a **Sections** toggle.
A review page's header has a **…** menu with
the pull request's conversation, files, and compare views on GitHub. Each file
card's header has one too: **View diff on GitHub**, **View file on GitHub**,
and, for a file in a project's folder, **View project**, which opens that
project on the site. That is the deepest project the site has a page for, so a
nested project owns its own files, not the project around it. **View file on
GitHub** shows the file as it is now: on the pull request's head branch while
it is open, and once the build finds it merged, on the branch it merged into,
since the merge usually deletes its own. That is the default branch, or a
stacked pull request's parent branch while that branch exists. Once GitHub
reports the parent's branch deleted, as merging the parent usually does, or
when the summary records no `baseRef`, it is the default branch. A pull request
closed without merging, or an unmerged one whose summary records no `headRef`,
links its head commit. A file the pull request deleted links the merge base,
the last commit that has it.
`search/` searches every document the site
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
`<number>/<head>/summary.json` against the `diff.patch` beside it, asking GitHub
for no diff, and skips and reports any summary that fails or has no stored diff.
It marks generated files by the `attributes.json` beside a summary, or by the
checkout's `.gitattributes` for a summary stored without one.
It checks each file stored under `head/` against every context and added
line of the file's diff, and drops one that disagrees with a
`::warning title=Context dropped::` line. It serves each file that passes
once, at `reviews/blobs/<id>.txt` under the site's base, where `<id>` is the
file's Git blob id, with each CRLF line ending turned into LF. The file's
entry in the page's `data.json` gets a `context` naming that address, the
file's line count, and each run of lines its hunks leave hidden. The build
prints how many files it served this way and their total size.
It reads only `summary.json`, so it also reports each summary stored only as
`spec.json`, the name older releases wrote. To show such a summary, publish it
again with a current release, as
[the site guide](site.md#republish-summaries-stored-as-specjson) describes.
Each site page loads its `data.json` when it opens, where a `site page` embeds
its data so it opens from disk.
The page puts the controls of each hidden run in the line-number gutter of the
`@@` header below it, and those of the run below the last hunk in a row of
their own. Expand down reveals the 20 lines below the hunk above, expand up the
20 lines above the hunk below, and expand all the rest of the run. Each header
rewrites its `@@ -a,b +c,d @@` range as lines join its hunk, and a closed run
between two hunks joins them under one header. Once any line of a file is
revealed, the gutter of its first header offers a control that collapses the
file back to its hunks as first drawn. The page fetches the file the first time
a reader expands one of its runs. A page `site page` writes has no site to
fetch from, so it draws no controls.

The Projects page also lists each project that an open pull request
proposes, so a plan in review is on the page where readers look for plans.
A pull request proposes a project when the diff of its newest summary adds
the project's lowercase `readme.md` under the projects directory, its head
branch is in this repository, and the checkout the site is built from has
neither a project of that name nor a file at that path. A repository's first
plan counts too, before the checkout has a projects directory. The build
rebuilds the plan from that diff, loads it as `project list` does, and
serves it at `projects/<name>/`, the address it keeps once it merges, with
the other Markdown files the diff adds in the project's folder. It never
serves a proposed HTML page, image, or data file, because an HTML page runs
on the site's origin, and a proposal has not been merged. The project sits
in its status group with a **Proposed** badge that links the pull request's
review. Each of its pages opens its content column with a banner that names
the pull request, its title and status, and the head the site shows, and its
GitHub links, the history link included, point at that head. Typing
`proposed` in the filter lists the proposed projects, and search marks their
pages with the badge. A stack segment whose pull requests carry out a
proposed project is headed by that project, linked to its proposed page,
whose sidebar lists the stack. A plan that does not
load is left out with a `::warning title=Proposed project skipped::` line
that names the pull request. When two open pull requests propose one name,
the lower number's plan is shown, and the banner names the other. The build
reads whether each pull request is open, and whether its head branch is in
a fork, from the status query it already makes. When `gh` cannot answer, a
pull request proposes nothing, and the build prints how many it left out.
`site build` counts the proposed projects in its summary line:

```console
$ project site build --out _site --summaries summaries-ref/summaries
…
wrote _site/index.html: 15 projects, 1 proposed project, 23 reviews, 0 reviews skipped
```

A proposed project leaves the site with the deploy that runs when its pull
request merges or closes.

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

`site build` and `site serve` take each option only spelled in full, so
`--prep` is refused rather than read as `--prepare`. The ask rule `init` adds
for these flags, `Bash(project site *prepare*)`, then matches every spelling
an agent can use.

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
`--summaries` serves a directory of summaries instead of the ref. It serves the
summaries in this checkout's own `refs/projector/summaries` too, the ones a
local `summary publish` keeps, beside the fetched ones, and reads a summary in
both from the local ref. While it runs it checks `README.md`, `docs/`, a
configured `projects.dir`, and the summaries every second and rebuilds
when any of them change;
`--no-watch` builds once. It finds the summaries ref again on every check,
so a server started before the first `summary publish` shows the summary
once that publish writes the ref. It also fetches the ref again every
minute, so a summary published from another checkout appears within about
a minute, as a Pages site shows it after the deploy that each publish
starts. A fetch that keeps failing is reported once, and again only when
the reason changes; `--no-fetch` turns the periodic fetch off too. The
periodic fetch never prompts for credentials, so a remote that needs typed
ones reports the failure instead, and no fetch touches `FETCH_HEAD`. A path
the build has no file for gets `404.html` with status 404, as GitHub Pages
answers it. `--base` serves the site under a path, as `site build` builds
it.

`site status` exits 0 and prints the site's URL, or with `--pr` the pull
request's review URL, when the repository has the site workflow on its
default branch and a GitHub Pages site. It exits 3 and says why when either is
missing, or when a private repository's site is public. `site workflow`
prints the workflow file a repository adds to its default branch once, or
writes it with `--write`. `--write` refuses, exiting 1, to overwrite a
workflow edited by hand, and `--force` replaces it anyway. The workflow runs
when a summary is published, when a push to the default branch changes
`README.md`, `docs/`, a configured `projects.dir` outside `docs/`,
`.projector.toml`, or the workflow itself, when a pull request in the
repository closes, merged or not, and on demand; `--branch` names the
default branch when `origin` does not record it.

A closed pull request deploys so that Reviews shows it as **Merged** or
**Closed** right away, rather than as open until some later deploy. The
workflow listens for `pull_request_target`, which GitHub runs from the
default branch's workflow file, on the default branch. So the
`github-pages` environment allows the deploy, and the action checks out the
default branch, never the pull request's code. A pull request from a fork
skips the deploy, and its run takes a concurrency group of its own, so it
cannot replace a deploy waiting in the `pages` group. `project check` warns
while the workflow is an earlier shape that Projector wrote, which does not
deploy when a pull request closes. Run `project init` to rewrite it, or
`project site workflow --write` where `site.enabled = false` keeps `init`
away from the site. A workflow edited by hand gets no warning, because
Projector cannot tell what its owner meant; compare it with
`project site workflow` and add the trigger yourself.

### Generated files

A summary page counts each changed file's lines as hand-written, test,
generated, or documentation, labels generated files, and starts every file but
a hand-written one collapsed. A file is generated when the repository's
`.gitattributes` marks it `linguist-generated`, which is how GitHub decides to
collapse it in a pull request's diff:

```text
site/assets/summary.js linguist-generated=true
yarn.lock -linguist-generated
```

Setting `linguist-generated`, bare or to any value but `false`, makes a file
generated. `-linguist-generated` or `linguist-generated=false` keeps a file
out, even one whose path looks generated. When the attributes do not mention a
file, its path decides: a name such as `package-lock.json`, `yarn.lock`,
`api.pb.go`, or `client_pb2.py`, or a path through a `gen/`, `generated/`,
`mocks/`, or `__generated__/` directory, makes it generated.

A Markdown, MDX, reStructuredText, or AsciiDoc file is documentation, except a
skill's `SKILL.md`. A skill is instructions an agent follows, so its
`SKILL.md` counts as hand-written and starts expanded.

`summary init`, `summary refresh`, and `site page` read the attributes with
`git check-attr` in the checkout they run in. `site build` and `site serve`
read them in the checkout `--repo-root` names, for each summary stored
without them:

- **The checkout has the pull request's head commit.** The head's own
  `.gitattributes` files answer, so a pull request that changes them is
  summarized as it changes them. Git 2.40 and later read them with
  `check-attr --source`. An earlier Git reads the head's tree into a
  temporary index and checks the attributes there.
- **The checkout lacks the head, but one of its remotes is the pull request's
  repository.** Its working tree answers.
- **Anything else**, such as a checkout of another repository or a directory
  outside one. The path alone decides.

Only the repository's `.gitattributes` files count, as on GitHub. The commands
leave out your global attributes file and the system's, and a pattern matches
a path's exact case even in a checkout on a case-insensitive file system.

A deploy checks out the default branch, which lacks the head of an open pull
request. So `summary publish` reads the head's attributes itself and stores
them beside the summary as `attributes.json`, which the site build uses in
place of its checkout's. When the checkout lacks the head, `publish` first
fetches it from the remote it publishes to. The fetch adds the commit's
objects and rewrites `FETCH_HEAD`, and changes no branch or tag. When the
remote cannot serve the head either, `publish` says so and stores no
attributes and no files under `head/`. A summary stored without them, as
every summary an earlier release published was, takes its attributes from
the default branch when the site builds.

## Review a pull request

`project review` runs the mechanical steps of the `review-pr` skill, so
a reviewing session does not rebuild them as helpers of its own:

```sh
project review setup 66 --model claude-opus-5-5 --loop example-review-claude-opus-5-5
project review move 66
project review census 66 --json
project review settle 66 --thread PRRT_kwDOAbc123 --result fixed --body reply.md
project review publish 66 --verdict clean --body body.md --covered 12/12 --loop example-review-claude-opus-5-5
project review interdiff 66
project review publish 66 --incremental --body body.md
project review release 66
project review finish 66
project review prune --apply
project review setup 66 --model claude-opus-5-5 --replace 4011
project review gate 66
project review status 66
project review suggestions 66
project review hold 66
project review ready 66
project review rules
project review workflow
```

`setup` checks that pull request 66 is open, fetches its head into the
checkout — from its branch, or from `pull/66/head` when the head is in a fork —
and fetches the base. It reads each fetched commit from a ref of its own under
`refs/projector/review-fetch/`, which it deletes at once. It neither reads
nor writes `FETCH_HEAD`, so another fetch in the same checkout cannot change
what it reads, and your own `FETCH_HEAD` stays as your last fetch left it.
It creates a scratch worktree at the exact SHA GitHub
reports, verifies the worktree's `HEAD`, computes the merge base, and posts the
start comment the skill describes as the reviewer. It records the SHA, the
merge base, the worktree, whether the head is trusted, and the start comment's
id and `created_at` in a state file, and prints them. Once the new worktree
exists, `setup` removes the worktrees of the model's earlier heads of the pull
request and prints a line for each, so a review keeps one checkout at a time.
It keeps an earlier head's worktree while that head's lock is held, since a
review may still be reading it. A head is trusted when it
is a branch in this repository, the pull request's author is you or has write
access, and no commit names another author; a fork's head, or an untrusted
one, is recorded so and reviewed by reading. `--rereview` records a re-review
of a head the loop already published a verdict on.

`--model` is the model ID of the session reviewing, and each model is a
reviewer of its own. Two IDs name the same reviewer when they match
lowercased and without a trailing bracketed variant, so `claude-opus-5-5`
and `claude-opus-5-5[1m]` are one reviewer and `gpt-5.5` is another; the
effort and the Projector version are not part of it. Each model keeps its own
state, lock, and scratch worktree, so two models review one head side by side
on one machine. The start comment's marker names the model with `model=`, and
`setup` refuses while another session's live start comment names the same
model, naming that comment. A start comment is live until its model posts a
verdict on its head, or until a day passes since it was last edited, the age
at which a lock is stale. `setup` refuses a model ID that is empty, holds
whitespace, or makes a folder name that starts with `.`, and deletes a start
comment this machine's state recorded for the model and never published.

When the session whose start comment stops `setup` has stopped, run `setup`
again with `--replace <comment-id>`, naming the comment the refusal named.
`setup` checks that the comment is a start comment the reviewer posted on
this pull request for the same model, and deletes it as the reviewer once
this review's start comment is posted. It refuses, exiting 1 and deleting
nothing, any other comment: another model's start comment, another account's,
a comment that is no start comment, or one that does not exist.

Once the start comment is posted, `setup` adds the `reviewing` label to the
pull request. It also removes the `clean` label when the newest counted
Projector verdict on the head is not clean, or when the head has none, as
after a push to a pull request that a review signed off. It prints
`added the reviewing label` or `removed the clean label` for each change, and
creates a label the repository lacks, as `publish` does. When GitHub refuses
the change, `setup` prints a `warning:` line on stderr and the review stays
set up. With `review.labels` set to `false`, `setup` leaves the labels alone.

The start comment and the review's signature line name the head under review
by its short SHA, the Projector version that `project version` reports, the
model from `--model`, and the reasoning effort from `CLAUDE_EFFORT` in the
environment, which Claude Code sets for the Bash tool and keeps current
through a mid-session `/effort` change. When `CLAUDE_EFFORT` is unset or
empty, as on Codex or on a model without effort support, they leave the
effort out rather than guess it.

`move` follows a head that moved mid-review: it creates a worktree for the new
head, rechecks trust, edits the start comment in place to name it, re-reads the
comment to confirm the edit, and updates the state file. It then removes the
worktree of the head it moved from, as `setup` does. `census` lists every
thread whose first comment carries the `projector-finding` marker, reading
every page, and prints how many are resolved and open and each open thread's
`path:line`; with `--json` each thread also carries its comments. It counts
every model's threads alike and takes `--model` like the commands around it,
so a reviewing session passes the same flags to each. `--model` and `--loop`
also pick the review whose lock `census` touches, as the lock paragraph below
describes. `release` clears the locks of one model's review of the pull
request.

`settle` answers one finding thread once the review has verified it at the
head under review. `--thread` is the thread's `id` from `census --json`,
`--result` says how the finding stands, and `--body` names a file holding the
reply's text. `settle` opens the reply with the verify marker, filled from
the review's state with the head and the model:

```
<!-- projector-verify v=1 result=fixed sha=<full-sha> model=claude-opus-5-5 -->
```

The watchers read that marker to tell the reviewer's reply from the author's
answer. `settle` posts the reply as the reviewer, then sets the thread by the
result:

| `--result` | The thread |
| --- | --- |
| `fixed`, `accepted`, `withdrawn` | Resolved |
| `reopened` | Unresolved |
| `kept` | Left as it is |

It reads the reply and the thread back, and exits 1 when the reply does not
open with the marker or the thread is not resolved or unresolved as the
result asks. When the thread's last comment already opens with the same
marker, as after a run that stopped once it replied, `settle` posts no
second reply and only sets the thread. It refuses, exiting 1 and posting
nothing, a thread that is not a review thread on the pull request, one whose
first comment carries no finding marker, an empty body, and a body that
carries a Projector marker of its own. A `--result` outside the five exits 2.
With `--json` it prints the `thread`, the `result`, the `sha` and `model` the
marker names, the reply's `comment` id and `url`, and whether the thread is
`resolved`.

`finish` ends one model's review of the pull request on this machine, once
the review is published and its summary has run, or when the pull request
closes. It takes these steps:

1. As the reviewer, it deletes every start comment of the model's that no
   longer means a review in progress. By the rule `setup` uses, a start
   comment stops meaning one once its model posts a verdict on the comment's
   head after the comment, or once a day passes since its last edit. Another
   model's start comments, and the model's live ones from another session,
   stay.
2. It removes the `reviewing` label once no start comment of the reviewer's
   means a review in progress, and prints `removed the reviewing label`. While
   another model's review is still in progress, the label stays. When GitHub
   refuses the change, `finish` prints a `warning:` line and goes on. With
   `review.labels` set to `false`, it leaves the label alone.
3. It removes every scratch worktree the review's state lists: the current
   head's, and any earlier head's that `setup` or `move` kept because its
   lock was held or it would not come out. It removes each with
   `git worktree remove` in the checkout, then runs `git worktree prune`. When
   the checkout is gone, it deletes a worktree under the state directory itself.
4. It clears the locks and deletes the state. The loop's record of published
   reviews stays.

`finish` acts on the repository `--repo` names, or else on the `origin` of
the checkout it runs in, as `setup` does. It never takes the repository from
review state, so a run in one repository cannot end another repository's
review of a pull request with the same number. Outside a checkout it needs
`--repo`. When no review of the pull request is set up for that repository
but a review of the same number is set up for another, it refuses until
`--repo` names the review to finish.

Without `--force`, `finish` also refuses while the state records a start
comment. That means either a review set up and not yet published, or a
publish that posted the review and stopped before it deleted the comment,
which running the same `publish` command again finishes. `--force` discards
that review and deletes its start comment too. Each refusal exits 1 before
anything is deleted. With nothing set up for the pull request on this
machine, `finish` asks GitHub nothing, prints that there is nothing to
finish, and exits 0, so running it twice is safe. With `--json` it prints the `repo`, `number`, and
`model`, the `comments` it deleted, the `worktrees` it removed, the locks it
`released`, whether it deleted the `state`, and the `labels` it changed, as
the label names `added` and `removed` and any `warning`, or `null` when it
had no label to remove.

`prune` sweeps the scratch worktrees that no review on this machine needs. A
review needs the worktree of its current head while its pull request is open,
and the worktree of any head whose lock is held and less than a day old.
Without `--apply`, `prune` lists each worktree it would remove and each one it
keeps, with the reason, and removes nothing. With `--apply`, it removes them:

| Worktree | Why it can go |
| --- | --- |
| `worktrees/<owner>/<repo>/<pr>/<model-key>/<short-sha>` other than the review's current head | The worktree of an earlier head |
| `worktrees/<owner>/<repo>/<pr>/<short-sha>` | Made by a release that kept no folder per model |
| Under a model key with no review set up | No review of the pull request by that model is set up here |
| The current head of a closed pull request | The pull request is closed |

`prune` asks GitHub only whether each pull request with a review set up here
is open. When GitHub cannot say, it keeps that review's worktree. Just before
it removes each worktree, it reads that review's lock and state again, so a
review that `setup` or `move` starts while `prune` runs keeps its worktree.
It leaves the review's state, locks, and start comment alone. To end a closed
pull request's review, run
`project review finish <pr> --repo <owner>/<repo> --force`. `prune` removes each
worktree with `git worktree remove` in the checkout that made it, and deletes
the folder outright when that fails. It removes only folders: a file or a
symbolic link where it looks for worktrees, such as a summary a reviewer saved
in a pull request's folder, is listed as kept. It then runs
`git worktree prune` in each checkout and removes the folders it emptied.
`--repo` limits the sweep to one repository. With `--json`
it prints the `repo`, whether it ran with `apply`, and the `removed`, `kept`,
and `failed` entries, each with its `path` and `reason`. It exits 1 when a
worktree could not be removed.

`publish` submits the review. `--body` names a file holding the review below
its signature line, with `{census}` where the census goes and, wherever used,
`{took}`, `{seconds}`, `{sha}`, and `{short_sha}`, which it fills in prose and
in a backtick span that holds one placeholder alone, but not in longer quoted
code; `--threads` names a JSON list
of findings, each `{"path", "line", "priority", "body"}`; `--covered` gives the
files read over the files changed. `publish` writes the signature line and
marker itself from the review's state and one reading of the clock, and adds
the finding marker to each thread.

A finding whose priority is `P3` is a suggestion, which opens no thread. Its
`line` may be left out and need not be in the diff, and its `body` is the
suggestion, which `publish` joins onto one line. `publish` ends the review
body with the suggestions in one form, which `review suggestions` reads back:

```markdown
<!-- projector-suggestions v=1 count=2 -->
### Suggestions

- `src/cache.py:42`: Name the helper `warm`, as the cache module calls it.
- `README.md`: Say that a cold start warms the cache.
```

The `projector-suggestions` line directly before the label counts the list,
for `status` and the watch daemon to read without parsing it. It is a line of
its own because every earlier release reads a review marker that holds a
field it does not know as no verdict at all. The review's marker keeps the
shape those releases read. A body that carries a suggestions section of its
own, as a review-pr skill from before P3 entries writes, publishes as
written, with the count line before its label. A review that lists no
suggestions has no count line. Where a review has none but lists
suggestions, as one an earlier release published does, `status` counts its
list.

`publish` refuses, exiting 1 and keeping the lock, when:

- the pull request closed or its head moved;
- a finding's line is outside the diff's hunks, or its text lacks a priority
  header or a `**Fix:**` line;
- a P3 has no path, a path holding a space, a colon, or a backtick, a `line`
  that is not a line number, or an empty body;
- the body is empty, carries its own signature or marker comment, or has no
  `{census}` outside quoted code;
- the threads file gives P3 entries and the body carries a suggestions
  section of its own, which would list them twice;
- the verdict disagrees with the finding count;
- the reviewer already has a verdict by this model on the head that this
  loop's record does not account for.

A verdict by another model is a second opinion and never collides, and one
whose marker names no model collides with every model. Outside a loop,
`--second-verdict <review-id>` publishes beside an earlier verdict the body
names.

On a self-review `publish` submits a `COMMENT`. On another author's pull
request it posts `REQUEST_CHANGES` or a `COMMENT`, or an `APPROVE` for a
clean verdict where `review.allow_approve` is `true` and the pull request is
not a draft, because an approval would clear a draft to merge while its
author is still working on it.

Once the review is submitted, `publish` reads it back and decides whether
the head is signed off. A head is signed off when the newest Projector
verdict on it, whichever model gave it, is clean and no Projector finding
thread is open. The newest verdict is usually the one `publish` just posted,
and is another model's only when that model posted on the head in between.
In the same step `publish` counts the open finding threads again, so a
finding that another model posts after this review's census still keeps the
head from being signed off. It counts the reviewer's verdicts, the one it
just posted included, and those of the accounts the site's status counts:
in a public repository, any account that owns the repository, belongs to its
organization, or collaborates on it, and in a private or internal
repository, every account. Draft state plays no part in the sign-off, and
`publish` never changes it, because a draft means that the pull request's
author is still working on it. The author moves it with `hold` and `ready`.

`publish` then acts on the sign-off in this order:

1. On another author's pull request whose head is signed off, it dismisses
   each of the reviewer's Projector `REQUEST_CHANGES` reviews that nobody
   dismissed and no later approval by the account replaced, because a
   `COMMENT` leaves the account's last change request holding
   `reviewDecision`. A change request whose body carries no Projector
   verdict marker is left alone.
2. When a ruleset on the repository's default branch requires the status
   check `projector/review`, it posts that commit status on the SHA it
   reviewed, even when the pull request's head moved after the submit. The
   status links the review whose verdict decided it, with the state and
   description in the table below. Without such a ruleset, `publish` posts no
   status.
3. It changes the labels in one `gh pr edit`. It adds `clean` when the head
   is signed off and is still the pull request's current head, and removes
   `clean` otherwise. It removes `reviewing` unless another start comment of
   the reviewer's still means a review in progress, such as another model's.
4. It adds the review id to the loop's record, deletes the start comment,
   and releases the lock.

| Head | State | Description |
| --- | --- | --- |
| Signed off | `success` | `Clean: claude-opus-5-5`, naming the model of the newest verdict |
| A finding thread is open | `failure` | `Changes requested: 2 open findings`, with the count |
| The newest verdict requests changes | `failure` | `Changes requested: claude-opus-5-5`, naming its model |

`publish` reads the rules with
`GET repos/{owner}/{repo}/rules/branches/{branch}`, which any account that
can read the repository may call. That endpoint reports rulesets only, so a
repository that requires `projector/review` through classic branch
protection gets no status. `publish` reads the default branch rather than
the pull request's base, so a stacked pull request's head already carries
the status when its base retargets to the default branch.

`setup` and `publish` create a label the repository lacks the first time
they add it:

| Label | Color | Description |
| --- | --- | --- |
| `clean` | `4eba65` | Projector's newest review of the current head is clean |
| `reviewing` | `d77757` | A Projector review of this pull request is in progress |

The colors are the ones the panel draws the two states in on Claude Code's
dark themes: `reviewing` in Claude's orange, which the light themes share,
since yellow marks a review's findings, and `clean` in the `success` green.
A light theme draws `clean` in a darker green. The review commands use a
label that already exists as it is, and never change its color or
description. `project init` gives a label the current color only while it
still has an earlier release's color and Projector's description, as
[Keep the review labels current](#keep-the-review-labels-current) describes.
`clean` says that Projector's newest review of the current head is clean,
not that the pull request may merge, because a repository can still require
people's approval. A label stays on the pull
request through a push, so `setup` removes a stale `clean` when the next
review starts. `publish` reads the head again after it adds `clean`, and
takes the label back off when a push moved the head during the edit. Set
`review.labels` to `false` to keep `setup`, `publish`, and `finish` from
adding or removing either label, as in a repository whose own `clean` or
`reviewing` label means something else.

`publish` prints what it did:

```
$ project review publish 66 --verdict clean --body body.md --covered 12/12 --loop example-review-claude-opus-5-5
published review 3301 (clean, COMMENT) on example/app#66 at 4f2a9c1d8e3b7a6f5c4d3e2f1a0b9c8d7e6f5a4b
4f2a9c1 is signed off
posted projector/review success on 4f2a9c1
added the clean label
removed the reviewing label
```

Before the sign-off line, it names the newer verdict that decided it when
that is another model's, and the open finding threads that kept a clean
verdict from signing the head off. The `posted` line appears only where a
ruleset requires the check, and a last line names any change requests it
dismissed. `--json` gives them as `signed_off`; `newest`; `open_threads`, a
count; `status`, the state it posted or `null`; `labels`, the label names
`added` and `removed` and any `warning`, or `null` with `review.labels` set
to `false`; and `dismissed`, the dismissed review ids.

A run that fails after submitting, including a dismissal or a status that
GitHub refuses, exits non-zero, and running it again finishes that review
rather than posting another. A refused label change does not fail the run:
`publish` prints a `warning:` line on stderr and exits 0, because the labels
only show the sign-off, and the status is what a ruleset enforces.

`status` prints the newest Projector verdict on the pull request's current
head, the one `publish` signs the head off by, with the model and the review
it came from. When several models gave a verdict on the head, it adds each
model's newest one, newest first. Two accounts that run the same model each
keep their own line, with the account in parentheses after the model, as
`claude-opus-5-5 (teammate)`. Each verdict gives the number of suggestions
that stand with its review when there are any: those the review lists and,
for an incremental review, those of the full review it builds on. These are
the suggestions `suggestions` lists for the head, picked the same way, so
after an incremental review the count does not drop to 0 while the full
review's suggestions wait on `fix-pr`. Then it lists the open finding
threads. Its last line says whether the head is signed off, adds `a draft`
when the pull request is one, names the Projector labels the pull request
carries, and gives the state of the `projector/review` status on the head
when it has one:

```
$ project review status 66
#66 at 4f2a9c1: clean with 2 suggestions from claude-opus-5-5, https://github.com/example/app/pull/66#pullrequestreview-3301
  claude-opus-5-5   clean               review 3301, 14:20 UTC, 2 suggestions
  gpt-5.5           changes-requested   review 3290, 14:02 UTC
  signed off; labeled clean; projector/review success
```

A head with no Projector verdict is `unreviewed`. On an unlabeled draft that
no review has signed off, the last line reads
`not signed off, a draft; no Projector label`. `status` counts the
verdicts `publish` counts: those of the reviewer, found as `setup` finds it,
from `--reviewer`, then `review.username`, then the authenticated user, and
in a public repository those of any account that owns the repository, belongs
to its organization, or collaborates on it, or in a private or internal
repository those of every account. It reads the pull request, its reviews,
its threads, and its head's commit status, and runs from any directory. It
exits 0 whatever the verdict, and 1 only when it cannot read the pull
request. With `--json` it prints one object:

| Field | Value |
| --- | --- |
| `repo`, `number`, `head` | The pull request and its current head SHA. |
| `draft` | Whether the pull request is a draft, which means its author is still working on it. |
| `verdict` | The newest verdict on the head: `clean`, `changes-requested`, or `unreviewed`. |
| `newest` | That verdict's `model`, `verdict`, `review` id, `url`, `at`, and `login`, and the number of `suggestions` that stand with its review: those the review lists and, for an incremental review, those of the full review it builds on. Each review's count is its `projector-suggestions` line, or its body's list where it has none. It is `null` when the head is `unreviewed`. |
| `models` | Each account's newest verdict by each model on the head in the same shape, newest first. A reviewing session finds its own by `login` and `model`. |
| `suggestions` | The number of suggestions every entry of `models` brings, each review counted once: the suggestions `suggestions` lists for the head. |
| `open_threads` | Each open Projector finding thread's `path`, `line`, and `model`, or `null` for a thread whose marker names none. |
| `signed_off` | Whether the head is signed off: `verdict` is `clean` and `open_threads` is empty. Draft state plays no part in it. |
| `labels` | The Projector labels on the pull request, `clean` and `reviewing`, in that order, or an empty list. |
| `check` | The state of the `projector/review` commit status on the head, such as `success` or `failure`, or `null` when the head has none. |

`suggestions` lists the suggestions the `fix-pr` skill decides on the pull
request's current head: those of each account's newest Projector review by
each model on the head, among the reviews `status` counts, newest first. An
incremental review lists none of its own, so after one it also lists those
of the full review whose link opens its lead. It prints a block for each
suggestion, naming the file and line, the head of the review it comes from,
and that review's URL, with the suggestion below:

```
$ project review suggestions 66
src/cache.py:42, in the review of 4f2a9c1: https://github.com/example/app/pull/66#pullrequestreview-3301
Name the helper `warm`, as the cache module calls it.

README.md, in the review of 4f2a9c1: https://github.com/example/app/pull/66#pullrequestreview-3301
Say that a cold start warms the cache.
```

It reads the section `publish` writes and every form a reviewer has written by
hand: a `Suggestions` heading of level 1 to 4, a `**Suggestions**` line in
bold, or a plain `Suggestions:` line, in any case. The section ends at the
next heading, line in bold, or label line such as `What was checked:`, or at
a paragraph after a blank line. Each list item is one suggestion, with the
lines under it joined to its text. An item that opens with no `path:line`, in
code font or bare, names no file. It finds the repository and the reviewer as
`status` does, and runs from any directory. With no suggestion it prints
nothing and exits 0. It exits 1 only when it cannot read the pull request or
its reviews. With `--json` it prints a list, empty when there is none, of one
object for each suggestion:

| Field | Value |
| --- | --- |
| `url` | The review that lists the suggestion. |
| `sha` | The head that review read, which for a full review an incremental review builds on is an earlier head. |
| `path`, `line` | The file and line the suggestion names, or `null` where it names none. |
| `text` | The suggestion, on one line. |

`hold` and `ready` move your own pull request's draft state. A draft means
that its author is still working on it. Run `ready` once your work on the
pull request is done, to mark it ready for review. Run `hold` to convert it
back to a draft while you build a change that no review has read, as the
`fix-pr` skill does before it builds a suggestion, and run `ready` again
once the push that carries the change lands:

```sh
project review hold 66
project review ready 66
```

Each command checks that the pull request is open and that `gh` is signed in
as its author, and refuses otherwise, because only the author's draft state
is theirs to move. It then runs `gh pr ready`, with `--undo` for `hold`,
reads the pull request back, and refuses when the draft state did not
change. A pull request that is already in the state you ask for is left as
it is. Run these commands rather than `gh pr ready` itself: they refuse a
pull request someone else wrote, they confirm the change, and the
`Bash(project *)` rule that `init` adds allows them.

Neither command touches the labels, so a held draft keeps its `clean`
label. Together, the label and the draft say that a review signed off the
head and that its author is building a change on top of it. Once the
change is pushed, `setup` removes the label when the next review starts.

`hold` and `ready` act on the repository `--repo` names, or else on the
`origin` of the checkout you run them in, and refuse outside a checkout
whose `origin` is on GitHub unless you pass `--repo`. Unlike `status` and
the commands that follow a review, they never take the repository from a
review set up on this machine, because a command that moves a pull
request's draft state must not act on a repository it guessed. Each prints
the draft state and the head that GitHub shows once the state has moved:

```
$ project review ready 66
marked example/app#66 ready for review at 4f2a9c1
$ project review hold 66
converted example/app#66 to a draft at 4f2a9c1
```

GitHub can take a few seconds to show a head you just pushed, so a command
you run right after a push can still name the head before it.

When the pull request is already in that state, the line reads
`example/app#66 is already ready for review at 4f2a9c1` or
`example/app#66 is already a draft at 4f2a9c1`. Every refusal exits 1 and
says why. With `--json` each command prints one object:

| Field | Value |
| --- | --- |
| `repo`, `number`, `head` | The pull request and its head SHA. |
| `draft` | Whether the pull request is a draft after the command: `true` for `hold` and `false` for `ready`. |
| `changed` | Whether the command changed the draft state. It is `false` when the pull request was already in the state asked for. |

`interdiff` decides whether the head may have an incremental review: a short
clean verdict on a head that changed little since this loop's last full review
of the pull request. It reads the loop's record, where `publish` stores each
review's merge base as `base` and, for an incremental review, the full
review's head as `from`. It measures the pull request's own change since that
full review's head `O`. When the merge base moved, it replays `O`'s change
onto the new merge base with
`git -c merge.conflictStyle=diff3 merge-tree --write-tree --name-only --merge-base=<old base> <new base> O`
and diffs the result against the head, so the base's own changes stay out
however the head moved. A conflicted file shows the conflict region, its
markers naming the new merge base, the old one, and `O` by full SHA, removed
and the author's resolution added. `interdiff` then checks the rules that need
no judgment, in this order, and refuses at the first that fails:
`review.incremental` is not `false`; the review's state names a loop; the head
is trusted; the loop's record holds a clean full review of the pull request,
and its newest verdict is clean and on another head; that full review is on
GitHub and its marker names this run's Projector version, model, and effort;
no finding thread is open; the change can be measured, which takes the full
review's recorded merge base, both heads in the checkout, and on a moved base
a Git that knows `merge-tree --merge-base` (2.40 or newer); the change touches
no binary file, symlink, or submodule; and when the merge base moved, a `gate`
run on this head exited 0. It prints the earlier full review, the `kind` of
move, any conflicted paths, the counts, `incremental: every rule passes` or
`refused:` with the rule, and the patch when it measured one. A refusal that
only the gate rule makes ends `(needs_gate)`. It exits 0 when every rule
passes and 1 otherwise. With `--json` it prints one object:

| Field | Value |
| --- | --- |
| `incremental` | `true` when every rule passes. |
| `reason` | The rule that failed, in words, or `null`. |
| `needs_gate` | `true` when the gate rule is the only one that fails. |
| `from` | The full SHA of the full review's head, or `null` when there is none. |
| `review` | The URL of that full review, or `null`. |
| `kind` | `push` when `O` is an ancestor of the head and the merge base did not move; `merge` when the head also contains a merge commit that brought in the moved merge base; `retarget` when the merge base moved with no such merge, as when a stacked pull request is retargeted after the one beneath it merges; `rebase` when `O` is not an ancestor and the merge base moved; `rewrite` when `O` is not an ancestor and the merge base did not move. `null` when the change was not measured. |
| `unchanged_commits` | For `rebase` and `rewrite`, `true` when every commit the full review read has a new commit with the same `git patch-id --stable`; `null` for the other kinds. |
| `conflicts` | The paths the replay conflicted in, `[]` when it was clean or not needed, or `null` when the change was not measured. |
| `insertions`, `deletions` | The change's line counts, or `null` when it was not measured. |
| `files` | The paths the change touches, empty when it was not measured. |
| `patch` | The change, or `""` when it was not measured. |

`publish --incremental` publishes the incremental review. It applies the same
rules to the live state, and `--body` names a file of at most 600 characters,
once its placeholders are filled, on what changed and what was checked. It
writes the signature line with `incremental from <short-sha>`, the review
marker with `verdict=clean`, `findings=0`, and `covered=<n>/<n>` for the files
the change touches, a `projector-incremental` line naming the full review's
head, the kind, and the counts, and a lead that links the full review and
states the change's size, the kind, and any resolved conflict. It always posts
a `COMMENT`, even where `review.allow_approve` is `true`, adds "A human
approval is what remains." on another author's pull request, and labels the
pull request `clean` when it signs off the head, as any clean verdict does. It
refuses, keeping the lock, when a rule fails, when the body is empty, too
long, or holds a signature line, a marker, or `{census}`, and when given
`--threads`, `--covered`, `--second-verdict`, or
`--verdict changes-requested`. Without `--incremental`, `--verdict` and
`--covered` are required, and a body that holds a `projector-incremental`
line is refused like one that holds its own marker.

`gate` runs the repository's validation gate: the shell command
`review.gate` names in `.projector.toml`, run with `sh -c` in the scratch
worktree, with the worktree and the merge base as `$1` and `$2` and as
`PROJECTOR_WORKTREE` and `PROJECTOR_BASE`. Its output passes through, on
standard error under `--json` so standard output carries only the JSON record;
`gate` exits with the command's status, and the state file records the
command, its exit status, the head, and when it ran. The command runs the
head's code, so it runs only on a head `setup` recorded as trusted. Without
`review.gate_checks`, `gate` refuses, exiting 1 without running anything, on
an untrusted head, whether or not `review.gate` is set, and when `review.gate`
is unset.

When `review.gate_checks` names the CI checks that run the repository's
suite, such as `check` or `test, lint`, `gate` runs the `review.gate` command
on a trusted head when it is set, and then waits for CI instead of running the
suite. A failing command decides the gate with its own status, and `gate`
reads no CI. Reading CI runs nothing on your machine, so `gate` reads it for an
untrusted head too, and leaves the command unrun there. It reads each named
check's latest run on the head from GitHub Actions, ignoring a check of the
same name that another app or a commit status posts, and looks every 20
seconds for up to 30 minutes. It exits:

| Exit | When |
| --- | --- |
| 0 | Every named check concluded `success`. |
| 1 | A named check concluded `failure` or `timed_out`. |
| 75 | CI gave no evidence about the head's code. The record's `reason` says why. |

CI gives no evidence when a named check is still running after 30 minutes or
concludes `cancelled`, `skipped`, `neutral`, or the like. It also gives none
when no run of the check can come: the head's workflow runs have all finished
without it, one waits for a maintainer to approve it, or none started in the
first two minutes. A call to GitHub that fails while `gate` reads CI gives
none either, with the error as the record's `reason`. Last, CI gives none
when the head changed a file that decides what the check runs. Those are the
workflow file GitHub records for the check's run, and every reusable workflow
and local action that file reaches through a `uses: ./` line. A bare `uses: ./` runs the action at the
repository's root, so every file in the repository decides that check. A
head that changes only another workflow, such as a release workflow, keeps
CI's evidence. When `gate` cannot tell which files decided the run, it gives
no evidence rather than guess. On exit 75, a review runs the repository's full
gate locally on a trusted head, and reads an untrusted one.

The state file's gate record then also holds `checks`, each named check's
`name`, `conclusion`, and `url`, with a `null` conclusion for a check that had
no completed run, and `source`, which is `ci` when CI decided the result and
`local` otherwise.

`rules` checks whether your own settings hold the rules `init` adds to
`~/.claude/settings.json`, and to `~/.codex/rules/projector.rules` where Codex
is installed, which allow Projector's commands, such as
`project review publish`, `project review hold`, and `project summary publish`,
and ask before the few that reach outside Projector's own state. It prints
each file after `allows` or `lacks`; with `--json` it prints `allowed` for the
whole check and for each file, and `summary_publish` for each file, which is
`true` when that file allows `project summary publish`. A file can lack newer
rules and still allow the publish, and stderr says so for a Codex file, so a
refused publish can be told apart from a missing rule: running `init` again
does not change a refusal that a matching rule did not prevent. It writes
nothing, takes no pull request, and
runs from any directory. It exits 0 when every file has the rules. It exits 1
when a file lacks them or cannot be read, says on stderr what is missing, and
asks for `project init` at a terminal, because `init` adds the rules only for a
person. The `review-repo` skill runs `rules` once before its first review, so it
can warn you before a publish is refused rather than after. A file that lacks
the rules means the host's automatic approval can refuse a publish, not that it
will: the repository's own settings, or settings your organization manages, can
still allow the commands.

`workflow` prints the `.github/workflows/projector-clean.yml` that `init`
writes, as this release renders it, so you can compare it with a copy you
edited, as [Require a clean review before merge](#require-a-clean-review-before-merge)
describes. It writes nothing, takes no pull request, and runs from any
directory.

No command takes a SHA: each comes from GitHub or the state file. `setup` works
from the checkout containing the working directory, or `--checkout`, and
refuses outside one; the repository is that checkout's `origin`, and `--repo`
must name the same one. Every later command reads the checkout, worktree, and
repository from the state file, finding it by `--repo` or by the pull request's
number, and by `--model` or the one model with a review of the pull request
set up on this machine, so it runs from any directory. `finish` is the
exception: it deletes comments, so it never takes the repository from the
state file, and outside a checkout it needs `--repo`. With several models
set up, each later command refuses, naming them, until `--model` picks one.
State an earlier release wrote is not read, so a review set up before an
upgrade runs `setup` again. A command that posts a review, posts or deletes
a start comment, settles a finding thread, changes a label, or posts the
`projector/review` status runs `gh` as the reviewer,
`--reviewer` or `review.username` or else the authenticated user, with
`GH_TOKEN` set to `gh auth token --user <reviewer>` for that call alone, so
the account the rest of the session uses is left alone. `hold` and `ready`
run as the authenticated user, who must be the pull request's author.

A review holds a lock for its pull request, head, and model from `setup` until
it is published, recording the host, the loop, and when it was taken, so a
second instance of the same model for the same head exits. A refused `setup`
releases it, a lock older than a day is stale, and `release` clears the locks
of one model's review left by a session that stopped. `setup`, `census`,
`settle`, `move`, `interdiff`, `gate`, and `publish` touch the lock as they
run, so its modification time shows when the review last made progress.
`gate` also touches it once a minute while the repository's command runs, so
a long gate never looks stalled. `census` touches the lock of the review that
`--model` names, or of the one model with a review set up here. `census`,
`interdiff`, and `gate` touch the lock only while it names their loop.

`--loop <id>` names the reviewing session that runs the review, by that
session's id, and the commands call it the review's loop. Each loop keeps its
record of published reviews under its id, and each entry names its model. The
id is up to 128 letters, digits, `.`, `_`, and `-`, starting with a letter or
digit, so any session id `project inbox` accepts is a loop id. `publish` adds
to the record under a lock on the record's folder, so a copy that
`project watch` makes into the same record at the same moment loses no entry.
A command without `--loop` runs in the loop that `setup` recorded, and a
review set up without `--loop` runs in no loop.

`project watch` hands a review to another loop by clearing its lock, when the
review stalls or the session that owns it lapses. So `settle`, `move`, and
`publish` refuse, exiting 1, when the head's lock names another loop than the
one they run in, or when the lock of a review that runs in a loop is gone:

```
project: the review of example/app#66 at 4f2a9c1 by model claude-opus-5-5 was handed over to another loop: its lock names loop 5f0c2d4e-91ab-4c3d-8e7f-2a6b1c9d0e3f, and this command runs in loop 08270a6c-e8dd-42c9-851b-ae1e507aa6dc; stop and report this rather than running `project review setup` again
```

Stop the review and report the refusal. Running `setup` again would take the
review back from the loop that owns it now. `publish` checks the lock again
before each write of the review's state and before it releases the lock, so
a hand-over that lands after it submitted the review leaves the new owner's
state and lock alone; the refusal then names the review it submitted. A
review that runs in no loop works as before: `settle` and `move` run without
a lock, and `publish` without one refuses and says to run `setup` again.

State, locks, loop records, and scratch worktrees live under
`$XDG_STATE_HOME/projector/reviews`, or `~/.local/state/projector/reviews`,
and `--state-dir` moves them. A model's
state and locks are under `prs/<owner>/<repo>/<pr>/<model-key>/` and its
worktrees under `worktrees/<owner>/<repo>/<pr>/<model-key>/`. The model key
is the ID lowercased and without its bracketed variant, with every character
but a letter, a digit, `.`, `_`, and `-` made `-`. Two IDs that make one key,
such as `a/b` and `a-b`, cannot both be set up for one pull request on one
machine: the second `setup` refuses. Every refusal exits 1 and says what to
do.

### Keep review labels from cancelling CI runs

Each review label fires GitHub's `labeled` event: `setup` adds `reviewing`,
and `publish` adds `clean`. In a repository whose CI starts on `labeled` and
cancels a pull request's earlier run, each label cancels the run in
progress. `project review ready` does the same in one whose CI starts on
`ready_for_review`. The `CHECKS` event and the panel's checks tell the two
kinds of failure in a cancelled workflow run apart by when each check
started. The run was cancelled when the first of its check runs that
concluded `CANCELLED` ended:

- **A check that started at or after the cancel** failed because of it.
  Only a job that runs `if: always()` to gather the jobs it needs starts
  then, since it waits for them to end. Projector always leaves it out.
- **A check that started before the cancel** failed on its own. Projector
  leaves it out only once a newer run of the same workflow on the head
  tests the code again: while the newer run is still running, or once it
  has run a check of the same name rather than skipping or cancelling it.
  The newer run's own result then stands for the code.

When Projector read none of the run's cancelled check runs, nothing says
when the cancel came, so each failure counts as one that started before
it. The cancelled runs still cost CI time, and the panel notes `cancelled`
on a pull request while a workflow's newest run on its head was cancelled.

Leave `labeled` out of the `types` of the workflow that runs your checks. A
job's `if` that skips the jobs for other labels is not enough. The
workflow's concurrency group cancels the earlier run before any job's `if`
is read. GitHub also counts a skipped job as a passing check, and the newest
check of a name as that check, so a label's skipped run would cover a
failure on GitHub, though Projector still reports it. When a label is meant
to start a fuller run, give that run a workflow of its own, so every other
label starts nothing that the checks' workflow counts. Name the label in its
concurrency group, so a review label cannot cancel the fuller run either:

```yaml
# .github/workflows/integration.yml
on:
  pull_request:
    types: [labeled]

concurrency:
  group: integration-${{ github.event.pull_request.number }}-${{ github.event.label.name }}
  cancel-in-progress: true

jobs:
  integration:
    if: github.event.label.name == 'run-integration'
    runs-on: ubuntu-latest
    steps:
      - run: make integration
```

## Use the inbox

`project inbox` keeps what an agent puts in front of you: questions, which
you answer, notices, which you read, and requests, work only you can do,
which you mark done or decline. The agent keeps working while an
item waits, and you can deal with it from any host. Each session's inbox is
its own directory under `$XDG_STATE_HOME/projector/inbox/`, or under
`~/.local/state/projector/inbox/` when `XDG_STATE_HOME` is unset, and every
command reaches it the same way from Claude Code, Codex, or a terminal.

An agent asks with one line, the answer it recommends, and any other options:

```console
$ project inbox ask "Decline the finding about the retry budget?" \
    --recommend "Decline it: the total cap already bounds retries" \
    --option "Add a budget of two retries per host" \
    --pr acme/app#337 --session "$SESSION"
3
```

The command prints the question's number at once. Each session numbers its
items 1, 2, 3, and on, in one count that every kind of item shares, so `3`
is the third item the session posted. `--pr` records the pull request the
item is about, as `owner/repo#number`, and does not change its number.
Projector's panel never shows the number, so in what you write to the person,
name an item by what it asks or tells. `ask` refuses, with exit code 65, a
question longer than one line or 120 characters, a question that holds a tab,
a missing recommendation, a recommendation or option longer than one line,
more than four other options, or an option that repeats the recommendation or
another option. Put what you need to decide in `--detail`, or `--detail-file`.

Every command names the posting session with `--session`, or reads
`PROJECTOR_SESSION`. Answering from a terminal needs neither: `list` shows
every session's open items, and `show`, `answer`, `dismiss`, `withdraw`,
`revise`, `snooze`, and `restore` find the session that holds the number.
`list` prints each item's number, kind, state, and subject, separated by tabs,
after the session when it lists every session. Every session counts from 1 and
keeps the items you resolved, so two sessions often hold the same number.
Without a session, the first six of those commands look first among open
items, the ones with no answer recorded: a number that one session holds open
names that session's item, whatever other sessions hold resolved under the
same number. `restore` looks first among the items it can bring back, the
dismissed and snoozed ones. Only when no session holds such an item under the
number do they look among every item. A command that
still finds the number in more than one session exits 67 and names them; run
it again with `--session` and the session that `list` prints beside the item.
A command waits up to five seconds while another command changes the same
session, and then exits 75, so you can run it again.

The answers are lettered. `a` is always the recommendation, the other options
follow as `b`, `c`, and on, and the last letter is a typed answer:

```console
$ project inbox show 3
3 Decline the finding about the retry budget?
about acme/app#337
a. Decline it: the total cap already bounds retries (recommended)
b. Add a budget of two retries per host
c. type an answer with --text
open
$ project inbox answer 3.a
answered 3.a
```

To give an answer of your own instead, use the last letter with `--text`, as
in `project inbox answer 3.c --text "keep the cap, but log each retry"`. A
typed answer is one line, like every answer. A question takes one answer; a
second exits 65. `dismiss` answers it with no letter, and `list` then shows
it as `dismissed`. Only `answer` takes a letter: every other command refuses
`3.a` with exit code 65, so `dismiss` never drops the answer you meant to
give.

In Projector's panel, only an option answers a question. The panel records an
answer when a prompt you send quotes the question and follows the quote with
one of its options' text and nothing else, as pressing the option in its
drawer fills it in. Anything else you send under a quoted question, such as your own
words, an option with more after it, or a question back, is a reply. The panel
records nothing, the question stays open, and the reply reaches the agent as
your prompt. The agent then withdraws the question if the reply settles it,
revises it if the reply shows it was unclear, or leaves it open while it still
needs you.

`answer`, `dismiss`, `snooze`, `done`, `decline`, `start`, `unclear`,
`discuss`, and `read` record your choice, your words, or that you read a
reply, so the permission rules `project init` adds ask you before an agent
runs one, in Claude Code, and leave them to Codex's usual approval.
Otherwise an agent could answer its own question, mark its own request done,
press start for you, say for you that you don't understand its item, write
in a thread as you, or mark its own reply read. `reply` records the agent's
own words, so an agent runs it without asking, in either host. Projector's
panel runs them itself, so answering there never asks. `restore` takes no
choice from you, so an agent runs it without asking, in either host, when
you say a dismissal was a mistake.

Every command refuses any other form of reference, such as `#337.3`,
`octo/web#12.1`, or `q2`, with exit code 65, and no command lists, delivers,
or deletes an item stored under one.

An answer reaches its agent through one deliverer, which claims it, sends it,
and marks it delivered. `watch` is the deliverer for any host that can run a
watcher: it prints one line for each answer and marks it delivered, so the
loop running it hears the answer:

```console
$ project inbox watch --session "$SESSION"
ANSWER 3.a Decline the finding about the retry budget? → Decline it: the total cap already bounds retries
DISMISSED 2 Run project init to add the publish rule?
RESTORED 2 Run project init to add the publish rule?
```

`RESTORED` tells the agent that you brought back a question it already heard
`DISMISSED` for, so the question is open again, as `restore` below describes.
`watch --once` makes one pass and exits. A Codex session needs no watcher:
the watch daemon claims each line its inbox owes and sends it with the
session's pull request events, as [Codex delivery](#deliver-events-to-codex)
describes. Recording an answer, outcome, dismissal, start, restore,
`unclear`, or `discuss` for a Codex session starts the daemon when it is not
running. A
view that delivers answers itself, such as Projector's panel in Claude Code,
uses the steps `watch` takes:

| Command | What it does |
| --- | --- |
| `claim <number>` | Claims an answer, word of a restore, or an `UNCLEAR` or `DISCUSS` line for delivery and prints a token and the text to send; exits 65 when it is already claimed or delivered |
| `claim <number> --renew <token>` | Keeps that claim live while its delivery is pending |
| `delivered <number> --claim <token>` | Marks the claimed answer, `UNCLEAR` line, or `DISCUSS` line delivered |
| `reopen <number> --claim <token>` | Clears the answer when its delivery failed, so the question is open again, or, for an `UNCLEAR` line, takes back the item's `unclear` mark, so you say it again, or, for a `DISCUSS` line, takes back your message, so you send it again |

A claimed `RESTORED` line is done after either `delivered` or `reopen`: no
deliverer sends it again, and the question stays open. A restore, or an
answer to a restored question, that lands while a line is being delivered
drops that line's claim, so `delivered` and `reopen` exit 65 for it, and the
next deliverer sends what the question owes then.

A claim that is not renewed for a minute lapses, so a deliverer that stops
halfway leaves the answer for the next one. A lapsed claim's token still
works until another `claim` takes the answer. `watch` marks an answer
delivered only after its line prints, so when nothing reads the watcher any
more, the answer waits for the next deliverer rather than being lost. Each
session keeps its 100 most recently delivered items, of any kind.

An agent tells you something that needs no answer with `tell`, such as the
answer to a question you asked it or something it did that you should know
about. A notice takes the same one line, `--detail`, `--pr`, and `--agent` as
a question, and the next number in the session's count:

```console
$ project inbox tell "Force-pushed #337 to drop a stray debug file" \
    --pr acme/app#337 --session "$SESSION"
4
```

`tell` refuses, with exit code 65, an empty notice, a notice longer than one
line or 120 characters, a notice that holds a tab, and a `--pr` that is not
`owner/repo#number`. A notice has no options, so `answer` refuses one with
exit code 65. So does `claim`, unless the notice owes an `UNCLEAR` line, as
`unclear` below describes. Once you have read it, run
`project inbox dismiss 4`. Nothing goes back to the agent, so the dismissal
also marks the notice delivered, and `list` shows it, as `dismissed`, only
with `--all`.

An agent asks you to do something only you can do with `request`, such as
running a command it may not run, signing in to a service, or checking a
change on your own device. A request takes one line, the steps you follow as
`--detail` or `--detail-file`, the same `--pr` and `--agent` as a question,
and the next number in the session's count. Each `--option`, up to four,
names a choice you pick from when you finish, and each marks the request
done; a request without one offers a single `Done`:

```console
$ project inbox request "Test the gutters on your phone" \
    --option "Looks good" --option "Some feedback" \
    --detail "1. Open Fyra. 2. Hold a track." --session "$SESSION"
5
```

`request` refuses an empty option, one of more than one line, one offered
twice, a fifth, and `Declined`, which declines a request. `revise` replaces a
request's options with `--option`, and `--no-options` removes them.

Once you have done the work, run `project inbox done 5`, or
`project inbox decline 5` when you will not do it. Either takes `--text`
with one line for the agent. The deliverer sends the agent `DONE 5` or
`DECLINED 5` and the request, with an arrow and your text after them when you
gave one:

```console
$ project inbox done 5 --text "Step 4 didn't work" --session "$SESSION"
done 5
$ project inbox watch --once --session "$SESSION"
DONE 5 Test the gutters on your phone → Step 4 didn't work
```

`request` refuses a request by the checks `tell` makes of a notice. A request
takes no answer, so `answer` refuses one with exit code 65, and `done` and
`decline` refuse any other kind of item, a resolved request, and a `--text` of
more than one line. `dismiss` declines a request with no text. A decline is a
dismissal, so `restore` reopens a declined request, and the agent that heard
`DECLINED` hears `RESTORED`. `list` shows a request as `open`, `unclear`,
`started`, `ready`, `blocked`, `done`, `declined`, or `withdrawn`.

When the agent must prepare something before you can begin, such as
installing a build on your phone, it gives the request a start action with
`--start`. Nothing happens until you say you are ready with `start`, which
takes the start action as you read it and refuses one that no longer
matches, so you consent only to what you read. The deliverer then sends the
agent `STARTED` with that action, and the agent does it and marks the
request `ready`, or `block`s it with what you should do first. Run `start`
again to retry a blocked request:

```console
$ project inbox request "Test the gutters" \
    --start "Install build c0d62a3b on your iPhone" --session "$SESSION"
6
$ project inbox start 6 --action "Install build c0d62a3b on your iPhone"
started 6
$ project inbox watch --once --session "$SESSION"
STARTED 6 Test the gutters → Install build c0d62a3b on your iPhone
$ project inbox block 6 --session "$SESSION" \
    --reason "The phone is locked. Unlock it and press retry."
blocked 6
```

`start` refuses, with exit code 65, a request with no start action, one
already started and not blocked, a resolved one, and an `--action` that
differs from the stored start action. `ready` and `block` refuse a request
that is not started or that is already ready or blocked, and `block` refuses
an empty reason or one longer than one line. `revise` takes `--start` to
change the start action, and an empty `--start ""` removes it. Revising a
started, ready, or blocked request returns it to open, so a start you
pressed for the old text never covers the new. A restored request returns to
open the same way. When you mark a request done or decline it before its
`STARTED` reaches the agent, the outcome takes its place, and the agent hears
only the outcome. A `STARTED` already on its way still arrives, and the
outcome follows it. When `STARTED` cannot be delivered, the start is taken
back, so you press start again.

To put off an item you cannot deal with yet, snooze it. `snooze` hides it for
an hour, or for the whole minutes that `--for` gives, up to a day (1440
minutes), and `--for 0` wakes it now. Projector's panel keeps a snoozed item
listed, muted, for 30 seconds, and then leaves it out until its time passes.
`list` shows it as `snoozed until <HH:MM>`, in local time, or as
`snoozed until tomorrow <HH:MM>` when it returns tomorrow:

```console
$ project inbox snooze 3 --for 30
3 snoozed until 14:32
$ project inbox snooze 4 --for 720
4 snoozed until tomorrow 02:02
$ project inbox list --session "$SESSION"
3	question	snoozed until 14:32	Decline the finding about the retry budget?
4	notice	snoozed until tomorrow 02:02	Force-pushed #337 to drop a stray debug file
```

A snoozed item can still be answered or dismissed, and either one clears its
snooze, so a resolved item never returns. `snooze` refuses, with exit code
65, an item already answered or dismissed, a negative `--for`, and a `--for`
longer than a day, and it changes nothing when it refuses.

When you dismiss or snooze an item by mistake, bring it back with `restore`:

```console
$ project inbox restore 3
restored 3
```

`restore` reopens the item in place. It keeps its number, its text and
options, the pull request it is about, when it was posted, and the subagent
that posted it, and Projector's panel lists it as open again. For a snoozed
item, `restore` ends the snooze and changes nothing else. For a dismissed
item, it clears the dismissal, so a later `answer` records an answer as if the
item had never been dismissed. A dismissal clears any snooze, and `restore`
does not bring that snooze back.

A dismissed question owes its agent a `DISMISSED` line. When that line has
not gone out yet, `restore` cancels it, and the agent hears nothing. When it
was delivered, or a deliverer had claimed it, the agent may have heard it, so
the deliverer then sends a line that starts `RESTORED 3`, and the agent knows
the question is open again. A snooze sends the agent nothing, and neither
does a notice's dismissal, so restoring either sends nothing too.

`restore` refuses, with exit code 65, an open item, which has nothing to
restore, an answered question, an item its agent withdrew, which is the
agent's to post again, and an answer letter such as `3.a`. A number no
session holds exits 66, a busy store exits 75, and `restore` changes nothing
when it refuses. In Claude Code, the agent restores an item with Projector's
`restore` tool when you tell it a dismissal was a mistake, and the panel
offers **Restore** and **Unsnooze** in the item's drawer.

An agent takes back an open item that no longer needs you with `withdraw`,
such as a question your reply settled or news that has gone stale:

```console
$ project inbox withdraw 3 --session "$SESSION"
withdrew 3
```

`withdraw` takes any kind. In one write, it records the item as dismissed
and withdrawn and marks it delivered, so nothing goes back to the agent, not
even a `DISMISSED` line for a question or a `DECLINED` line for a request.
Projector's panel drops the item at once, and `list --all` shows it as
`withdrawn`. `withdraw` refuses what
`dismiss` refuses: an answer letter, such as `3.a`, and an item already
answered, dismissed, or withdrawn, with exit code 65, and a number no session
holds, with exit code 66. In Claude Code, the agent withdraws an item with
Projector's `withdraw` tool rather than from the shell.

When you don't understand an open item, say so with `unclear`, and the agent
that posted it revises the item to explain it more simply or to add the
context you lack:

```console
$ project inbox unclear 4
unclear 4
$ project inbox list --session "$SESSION"
4	notice	unclear	Force-pushed #337 to drop a stray debug file
```

`unclear` takes an open item of any kind. The deliverer sends the agent a
line that starts `UNCLEAR 4` with the item's line, and `list` shows the item
as `unclear` until the agent revises it. The revision clears the mark, so you
can say it again of the new text. `--subject` and `--detail` give the text
you read, and `unclear` refuses, with exit code 65, a line or detail that
differs from the item's, because the agent revised it after you read it. It
also refuses an item already marked unclear and one already answered,
dismissed, or withdrawn. An answer, outcome, or dismissal recorded before
the `UNCLEAR` line goes out takes its place, so the agent hears only that.
When the line cannot be delivered, the mark is taken back, so you say it
again. In Projector's panel, **I don't understand** in an item's drawer
fills the prompt for you to say what you don't follow. Once you send that
prompt, the panel runs `unclear` with the text you read and marks the line
delivered, because your prompt carried the `UNCLEAR` line and your words to
the agent.

When your reply shows that an open question was unclear, or that the choice
has changed, the agent rewrites the question in place with `revise`. Only the
parts it names change:

```console
$ project inbox revise 3 --session "$SESSION" \
    --question "Decline the finding about the per-host retry budget?" \
    --option "Add a budget of two retries per host" \
    --option "Ask the reviewer which hosts they mean"
revised 3
```

`--question` replaces the line, and `--recommend` replaces answer `a`. The
`--option` flags replace every other option, lettered `b`, `c`, and on in the
order given, and `--no-options` drops them all, which leaves only the
recommendation. `--detail`, or `--detail-file`, replaces the detail, and
`--detail ""` clears it. The question keeps its number, the pull request it is
about, when it was posted, the subagent that asked, and any snooze, so its
answer reaches the same agent as before. A request is revised the same way,
with `--request` in place of `--question`, and a notice with `--notice`.
Each kind takes `--detail`, and any revision clears the item's `unclear`
mark. `revise` refuses, with exit code 65, a flag that another kind takes:
`--question`, `--recommend`, or `--notice` for a request, `--request`,
`--start`, or `--notice` for a question, and anything but `--notice` and
`--detail` for a notice. It also refuses, with exit code 65, a revision that
fails a check its posting made, such as a line of 121 characters or a fifth
option, a revision with nothing to change, an answer letter such as `3.a`,
and an item already answered, dismissed, or withdrawn, and it changes
nothing when it refuses. A number no session holds exits 66. In Claude
Code, the agent revises an item with Projector's `revise` tool, and the
panel redraws it at once.

### Discuss an item

Each item can carry a thread: the messages you and the agent exchange about
it, oldest first. To talk an item over outside the session's conversation,
such as from a terminal, add your message to its thread with `discuss`:

```console
$ project inbox discuss 3 --text "Which hosts does the reviewer mean?" --session "$SESSION"
discussed 3
$ project inbox watch --once --session "$SESSION"
DISCUSS 3 Decline the finding about the retry budget? → Which hosts does the reviewer mean?
```

The item's deliverer sends the agent a line that starts `DISCUSS 3` with
the item's line, an arrow, and your message. The item stays open. Each
message waits behind any line the item already owes, its answer or outcome,
`RESTORED`, `STARTED`, or `UNCLEAR`, and an item's messages go out one at a
time, oldest first. A message reaches the agent even when you dismiss the
item before it goes out. When the line cannot be delivered, the message is
taken back, so you send it again.

The agent answers into the thread with `reply`, or with Projector's `reply`
tool in Claude Code, and in its conversation as well:

```console
$ project inbox reply 3 --text "Only the two mirrors behind the proxy." --session "$SESSION"
replied 3
$ project inbox list --session "$SESSION"
3	question	replied	Decline the finding about the retry budget?
$ project inbox show 3 --session "$SESSION"
3 Decline the finding about the retry budget?
about acme/app#337
a. Decline it: the total cap already bounds retries (recommended)
b. Add a budget of two retries per host
c. type an answer with --text
you: Which hosts does the reviewer mean?
agent: Only the two mirrors behind the proxy.
replied
$ project inbox read 3 --session "$SESSION"
read 3
```

A reply goes to no deliverer, because you read it where the item is shown.
`list` shows an open item with a reply you have not read as `replied`, and
`read` records that you read the item's replies. Projector's panel runs
`read` when you open the item's drawer.

To talk about something that has no item yet, start a discussion. Without a
number, `discuss` posts an item of kind `discussion`, with your message as
the first entry of its thread, and prints its number. With `--pr`, the
discussion is about that pull request, and its line is
`About owner/repo#number`. Without `--pr`, it is about the session itself,
and its line is `About this session`:

```console
$ project inbox discuss --pr acme/app#337 --text "Why the second retry loop?" --session "$SESSION"
7
$ project inbox discuss --text "Pause after this pull request." --session "$SESSION"
8
$ project inbox watch --once --session "$SESSION"
DISCUSS 7 About acme/app#337 → Why the second retry loop?
DISCUSS 8 About this session → Pause after this pull request.
```

A discussion has no options, and it stays open until you dismiss it or the
agent withdraws it. `answer`, `done`, `decline`, `start`, and `revise`
refuse one with exit code 65. Dismissing a discussion sends the agent
nothing, as dismissing a notice does, and `snooze`, `restore`, and
`unclear` work on it as on any item.

Each message is one paragraph of at most 4 KiB in UTF-8, with no line
breaks. `discuss` and `reply` refuse, with exit code 65, an empty message, a
longer one, and one with a line break. They refuse an item already answered,
dismissed, or withdrawn too. `discuss` refuses a number together with `--pr`
and records nothing, and it needs `--session` to start a discussion.

## Use the console

`project console` shows every live session in one terminal, and opens any one
of them as a panel, where you answer and discuss its items and pull requests.
It works the same for Claude Code and Codex sessions, so it is how you answer
a Codex session, which has no panel of its own. Run it in a split or a window
of its own:

```console
$ tmux split-window -h project console
```

The console opens on the home list, one entry per live session. A session is
live while its deliverer renews it: Projector's panel in Claude Code, shown
as `claude`, or the watch daemon's Codex adapter, shown as `codex`. Each
entry shows the session's name, or its short ID when it has none, its host,
its repository and branch, and its summary, as `project agent list` shows
them. Then come the counts of its pull requests, review requests, and open
items, and the subject of its newest open item. The sessions that need you
come first:

1. Sessions with an agent reply you have not read, whose entry says
   `replied` beside the item that holds it.
2. Then sessions with open questions and requests, the most first.
3. Then the rest.

Ties go to the session whose newest item is newest, then to the name.

Press Enter on a session to open its panel. The panel lists the pull
requests the session subscribes to, as Projector's panel does, and then its
inbox items, newest first. Each item line shows its subject, its state words
(`sending`, `replied`, `unclear`, and a request's `starting`, `ready`, or
`blocked`), its pull request, and its age. Press Enter on a row to open its
drawer:

- **An item's drawer** shows the item's detail and its thread, then its
  controls. A question offers `1` and on for its options, the first marked
  `(recommended)`, and `t` to type an answer of your own. A request offers
  `1: Start`, or `1: Retry` once its agent reports it blocked, when it has a
  start action, and then its outcomes, or `Done`. Every open item offers
  `6: I don't understand`, `7: Discuss`, `8: Dismiss`, and `9: Snooze`,
  which asks for 15 minutes, 1 hour, 4 hours, or 1 day. A dismissed item
  offers `8: Restore`. Opening the drawer marks the agent's replies read,
  as `project inbox read` does.
- **A pull request's drawer** shows the session's role, the branch, the size,
  the checks, and the labels, then the unresolved review threads by file.
  `7` starts a discussion about the pull request, `8` asks before it stops
  this session watching the pull request, `o` opens the pull request on
  GitHub, and `p` opens its Projector summary when its description links
  one.

With no drawer open, `7` starts a discussion about the session itself, so you
can talk to a session that has no item and no pull request. Discuss and `t`
open a one-line text field at the bottom of the screen.

| Key | Effect |
| --- | --- |
| Up and down, or `k` and `j` | Move between rows |
| Enter | On the home list, opens the session's panel. In a panel, opens or closes the drawer of the row under the cursor |
| Esc or `0` | Closes the drawer, or, with no drawer open, returns to the home list |
| Left | Returns to the home list from a panel, closing any open drawer |
| `7` in a panel with no drawer open | Starts a discussion about the session |
| Enter in a text field | Sends the message. Esc cancels it, and Left and Right move the cursor |
| `q` | Quits |

The console records each choice with the same functions `project inbox`
runs, for the session on screen: an option is `project inbox answer`, `7` is
`project inbox discuss`, and so on. So two sessions that each have an item 3
never mix up, and a refusal, such as an answer to an item its agent just
withdrew, shows at the bottom of the screen. The console never sends a line
to a session itself. The session's deliverer brings each line in: Projector's
panel in Claude Code within 5 seconds, or the Codex adapter, which the
console wakes by starting the watch daemon when it is not running. An item
reads `sending` until its line is delivered, and an answered item leaves the
list 30 seconds later. A discussed item stays open, and the agent's reply
appears in its thread.

Dismissing a pull request appends the `!` lines `project watch drop` appends,
for each role the viewed session holds on it, so another session that watches
the same pull request keeps it. The console lists only the pull requests a
session subscribes to. Projector's panel also lists those that an older
`watch-prs.sh` or `watch-threads.sh` watcher tracks, which the console cannot
see.

The console reads the inbox store each second, and reloads a session's items
only when its directory changed. It reads the pull request rows from the
watch daemon's cache every 5 seconds, so they cost no GitHub query. An open
pull request drawer asks GitHub for its threads when it opens and every 30
seconds while it stays open, at about one point of your hourly GraphQL quota
each, and asks nothing when the cache counts no unresolved thread. The console
draws in your terminal's own colors, and draws none when `NO_COLOR` is set.

Without a terminal on standard input and standard output, `project console`
exits with code 69 and says it needs one. On a Python without the `curses`
module, as on Windows, it exits with code 69 and points you to
`project inbox list` and `project watch list`. The permission rules
`project init` adds ask you before an agent runs `project console` in Claude
Code, and leave it to Codex's usual approval, because the console records
your answers.

## Watch pull requests

`project watch` subscribes an agent session to the pull requests it fixes and
reviews, and delivers what happens on them. One daemon per machine,
`project watch serve`, polls GitHub for every pull request that a live
session subscribes to, and appends each event to that session's `events`
file. You never start the daemon yourself: every `project watch` command but
`serve` and `stop` starts it when none is running, and it exits 5 minutes
after the last session lapses. Its files live under
`$XDG_STATE_HOME/projector/watch/`, or under `~/.local/state/projector/watch/`
when `XDG_STATE_HOME` is unset.

Each command acts for the session that `--session` names, then
`PROJECTOR_SESSION`, then `CLAUDE_CODE_SESSION_ID`, which Claude Code sets in
every command it runs, a subagent's included. So a subagent's command
subscribes the top-level session. An id holds letters, digits, `.`, `_`, and
`-`. A command that needs a session and finds none exits 2.

### Subscribe a session

`add` subscribes the session to a role on a target. The target is
`owner/repo#number`, a pull request's URL, or, for `--role review`,
`owner/repo`. `--role` is `fix` by default:

```console
$ project watch add acme/app#12
added acme/app#12 fix
events: mod
$ project watch add acme/app --role review --model claude-opus-5-5
added acme/app review claude-opus-5-5 octocat
events: mod
```

Each subscription is one line of the session's `subscriptions` file:

| Line | Subscribes the session to |
| --- | --- |
| `acme/app#12 fix` | The pull request's findings, verdicts, conflicts, review bodies, and merge or close |
| `acme/app#13 fix auto` | The same, added by a push, as `--from-output` adds it |
| `acme/app#14 review <model> <reviewer>` | The pull request's new heads and its author's answers, for one model and the login its reviews post as |
| `acme/app review <model> <reviewer>` | Every open pull request in the repository that the operator wrote or that requests the operator's review by login, including pull requests opened later |
| `!acme/app#9 fix` or `!acme/app#9 review <model> <reviewer>` | Nothing: the pull request stays out of that role for this session, wherever the line sits |

References match without regard to case. `--role review` needs `--model`, the
model ID the session reviews with, and resolves the reviewer from
`--reviewer`, then `review.username`, then the account `gh` is signed in as.

`add` changes nothing when the session already has the subscription, and
says so. It refuses, with exit code 65, a pull request the session drops
from that role; run `project watch restore` first. An explicit `add` of a
pull request the session has as `fix auto` drops the `auto` mark, which lets
the session take the `fix` role from another session (see
[Who hears an event](#who-hears-an-event)).

The last line of `add` says how the session's events arrive: `events: mod`
when Projector's mod in Claude Code renews the session, `events: stream`
when a running `project watch stream` renews it, `events: codex` when the
local Codex queue adapter handles it, or
``events: none — start `project watch stream --session <id>` under your
host's monitor facility`` when nothing has renewed it in the last 5
minutes. With `--json`, `add` prints the lines it `added` and the
`delivery`, which is `mod`, `stream`, `codex`, or `none`.

For a Codex session, `add` also asks `codex app-server` whether Codex runs
Projector's five hooks. When Codex does not run them, because they are
untrusted, modified, disabled, or missing, `add` names the problem and
what to do after the `events:` line, and in `codex_hooks` with `--json`.
`codex_hooks` is `null` when there is nothing to report.

An `add` that writes a line, or finds the line it would write already
there, the Codex hooks' subscriptions included, also registers a session
that is not registered yet for agent messages, with a summary made from its
subscriptions, unless `agent.auto_register` is `false`. The output of `add`
stays the same either way, as
[Registered when it subscribes](#registered-when-it-subscribes) describes.

### Deliver events to Codex

With the `projector-hooks` plugin installed in local Codex, review and trust
its command hooks through `/hooks` or the desktop app's hook settings. The
hooks ship apart from the main plugin, so a Projector upgrade leaves that
trust in place. Use
Codex CLI 0.166.0 or newer with a running local daemon. Projector checks
that the native executable supports `queue --thread`, `--message`, and
`--resume`. Remote app-server selection and Windows batch-file shims are
not supported by this adapter; use stream delivery where available.

The hooks register the real parent session ID, which workers share, and
subscribe after output from a push or PR creation confirms a target.
`watch.auto_start = false` disables those automatic subscriptions.
The same review and fix skills run on both hosts. Claude Code keeps its
mod and requires no Codex executable.

Codex receives one batch at a time, with standing work IDs that coalesce
reminders even when their text differs. An acknowledgment marks a batch
consumed. An idle session starts no model turn until an event arrives.
The registration expires after 24 hours without host activity.

The same batches carry the session's inbox lines, such as `ANSWER 3.a …`.
The daemon claims each line before it queues it and renews the claim until
Codex takes the batch, so no other deliverer sends the line too, and it
marks the line delivered when the batch is consumed. A line whose item
changed first, through a restore, another answer, or a revision, leaves
the queue, and the line the item owes now goes out instead. A failed send
keeps the claim and retries, so your answer stays recorded. A typed answer
longer than a batch goes out in a batch of its own. When delivery
disconnects, the daemon stops renewing those claims, and the session's
next deliverer sends the lines once the claims lapse a minute later.

Codex unloads a conversation that sits idle and unopened for 30 minutes,
and sends `SessionEnd` when it does. Projector keeps delivery enabled
through that unload: the daemon goes on watching the session's pull
requests, and `codex queue --resume` loads the thread again when a batch
arrives. A registered agent stays reachable the same way, so a peer's
message wakes it. Codex sends the same `SessionEnd` when you archive or
delete a conversation, so Projector instead checks that the conversation's
transcript still exists. Once it is gone, the session lapses, `watch status`
reports it `disconnected`, and Projector never resumes the archived
conversation. Resuming the conversation reconnects it. To stop delivery
yourself, use `project watch disconnect`, or remove the subscription.

The `UserPromptSubmit` hook acknowledges each batch as its turn starts.
When that acknowledgment never arrives, the daemon looks for the batch's
marker in the conversation's transcript, from where the transcript ended
when the batch was submitted, and marks the batch consumed once it
appears, so one lost acknowledgment cannot hold back later batches.

A daemon exit while Codex is idle needs the next hook or a watch command
to restart it:

```sh
project watch status --json
```

The `deliveries` object reports the state, expiry, batch ID, and diagnostic
for each Codex registration, and `unloaded_at` while Codex has the thread
unloaded. `codex_hooks` names what keeps Codex from running Projector's
hooks, or is `null`. `queued` means Codex accepted the message;
it does not prove the thread resumed. Resume that thread when its warning
says it needs attention. `uncertain` means a timeout or interrupted
submission left acceptance unknown. Projector resends an uncertain batch
30 minutes after submitting it, unless the conversation's transcript shows
that it arrived first. A copy that arrives after the batch is consumed is
blocked before it starts a turn. To resend sooner, check the thread, then
retry:

```sh
project watch codex-retry --session <id>
```

A retry can duplicate an accepted message. Projector suppresses duplicate
consumed batch IDs for at least 24 hours. To switch to a stream, stop any
existing host deliverer, then disconnect before starting it:

```sh
project watch disconnect --session <id>
project watch stream --session <id>
```

Disconnect keeps subscriptions and pending events. The stream takes
unsent events, while accepted and uncertain batches stay separate until
acknowledged or resent. Remove a PR's subscription to stop its
work, rather than merely changing its transport. A message Codex already
accepted cannot be recalled; the skills recheck subscriptions before acting.
When hooks are unavailable, `events: none` reports the stream fallback.
A background stream prints events but does not itself wake an idle Codex
session.

`project watch codex-hook` reads host hook JSON from standard input. The
plugin runs it at session start and end, prompt submission, shell-tool
completion, and turn stop. Malformed input produces advisory feedback;
it cannot register a target or start the daemon. Hook registration alone
adds no subscription and starts no watcher.

### Subscribe from a command's output

`add --from-output FILE` reads a command's output from the file, or from
standard input when `FILE` is `-`, and subscribes the session to `fix auto`
on each pull request the output shows it opened or pushed to:

```console
$ git push 2>&1 | project watch add --from-output -
added acme/app#12 fix auto
events: mod
```

It finds each pull request URL, as `gh pr create` prints one, and each
`To <remote>` block of a `git push` to GitHub with its
`<old>..<new>  <source> -> <branch>` lines. For each pushed branch, it asks
GitHub for the open pull request whose head is that branch, one REST
request, and subscribes to it. A branch with no open pull request adds
nothing, and a pull request the session drops from `fix` is skipped. A
lookup that fails is named on stderr, and the command still exits 0. When
`watch.auto_start` is `false`, it adds nothing and says so.

### End or exclude a subscription

`remove`, `drop`, and `restore` take a target and `--role`, which is `fix`
by default, and print each line they change:

| Command | What it changes | When to run it |
| --- | --- | --- |
| `remove` | Removes the session's subscription lines for the target and role, and leaves its `!` lines. | To stop fixing or reviewing a pull request, or with `--role review` and `owner/repo`, a repository. |
| `drop` | Appends a `!` line for the pull request and role. | To keep a pull request out of the role for this session, as dismissing it in the panel does. |
| `restore` | Removes the session's `!` lines for the pull request and role. | To let a dropped pull request back in. |

For `--role review`, `--model` limits the command to one model. Without it,
`remove` and `restore` cover every model, and `drop` covers every model the
session reviews the pull request with; `drop` refuses, with exit code 65, a
pull request the session does not review. With `--json`, each prints the
lines under `removed`, `dropped`, or `restored`.

### Deliver events

A session is live while something renews it, and the daemon polls only for
live sessions. A session nothing has renewed for 5 minutes lapses: the
daemon stops polling for it and hands its roles to other sessions. Renewing
it again makes it live, and the daemon announces what is outstanding for
it. The daemon counts the 5 minutes on its own clock and forgives a gap in
its own loop, so a machine's sleep lapses no session.

- **`renew --deliverer mod|stream`** marks the session live. Projector's mod
  runs it when a Claude Code session starts and at least once a minute.
- **`take`** prints and empties the session's events and renews the session.
  With `--json`, it prints `{"session": "<id>", "events": ["<line>", ...]}`.
  The mod runs it when the session's `events` file is not empty, and submits
  the lines as one prompt.
- **`stream`** renews the session every 30 seconds and prints each event as
  it arrives, until you stop it. Run it under your host's persistent process
  or monitor facility on a host without the mod, such as Codex. It also
  starts the daemon again if the daemon died.

### Who hears an event

The daemon gives each event to one session, the owner, for each pull request
and role: one owner for `fix`, and one for `review` for each model. The
candidates are the live sessions with a subscription that covers the pull
request in that role and no `!` line that excludes it. Among them:

1. A subscription to the pull request beats a subscription to its
   repository.
2. Among equals, the subscription added most recently wins.
3. A `fix auto` line never outranks a subscription that existed when it was
   added, so a session that pushes one fix to another session's pull
   request does not take the pull request from it.

When the owner changes, the new owner hears at once what is outstanding, and
a former owner that is still live gets `MOVED`. A `review` role moves only
between reviews. While a review lock for the pull request and model names
the owner, the owner keeps the role. When that lock has not changed for
`watch.review_stall` seconds, the daemon clears it and moves the role to the
session that outranks the owner, or, when none does, announces the head to
the owner again. When an owner lapses, the daemon clears its review locks at
once. At every move of a `review` role, the daemon copies the pull request's
entries from the former owner's record of published reviews into the new
owner's.

Each event is one line, `<KIND> <owner/repo#number> <detail> — <action>`,
which ends with what the session should do:

```text
FINDING acme/app#12 docs/x.md:40 [bugbot]: P2 the link is broken — run fix-pr on acme/app#12 in subagent fix-12
NEW HEAD acme/app#14 (feature): 4f2a9c1 -> 9f3c2a1, needs an exact-head review — run review-pr on acme/app#14 in subagent review-14 with --loop 08270a6c
MOVED acme/app#14 review for claude-opus-5-5 to another session — stop subagent review-14 and run no project review finish
```

| Kind | Role | Reports |
| --- | --- | --- |
| `FINDING` | `fix` | An unresolved review thread |
| `VERDICT` | `fix` | A standing `CHANGES_REQUESTED` review |
| `CONFLICT` | `fix` | A branch that conflicts with its base |
| `CHECKS` | `fix` | CI failing on the head, naming each check run that concluded `FAILURE`, `TIMED_OUT`, or `STARTUP_FAILURE` and each commit status in the state `FAILURE` or `ERROR`; the `projector/review` status does not count, and neither does a run that waits for approval or was cancelled or skipped, nor a failed check run of a cancelled workflow run that started at or after the cancel, as an `if: always()` job that gathers the others does, or that a newer run of the same workflow on the head tests again, by still running or by running a check of the same name rather than skipping or cancelling it. The cancel is when the first of the run's check runs that concluded `CANCELLED` ended |
| `REVIEW` | `fix` | A Projector review whose suggestions stand on the current head, with their count, or any other reviewer's comment review of the current head with a body |
| `LABEL` | `fix` | A `clean` or `reviewing` label added or removed since the owner last saw the pull request, as `+clean` or `-reviewing` |
| `UNREVIEWED` | `fix`, when no session on this machine reviews the pull request | A pull request ready for review whose head is not signed off, that no session here will review, and that no live start comment, from the operator or another account whose review would count, shows under review on any machine, once per head |
| `NEW PR` | `review` | A pull request the session now owns whose head this model has not reviewed; under a repository subscription, only once the pull request is ready for review |
| `NEW HEAD` | `review` | A head this model has not reviewed, or one whose review stalled; under a repository subscription, only once the pull request is ready for review, except that a review that stalls on a draft is reported once, to stop its subagent until the draft is ready |
| `RESPONDED` | `review` | A head whose newest verdict requests changes, once the author answers on it without pushing: every review thread is resolved, or the author replied on an open finding; under a repository subscription, only on a pull request ready for review |
| `MOVED` | any | Another session owns the role now |
| `MERGED`, `CLOSED` | any | The pull request merged or closed |
| `MISSING` | any | The pull request or repository does not exist, or the operator cannot see it |

A `LABEL` line is not work. The review commands set the `clean` and
`reviewing` labels, and any work behind a change to them arrives as its own
line. The daemon records the labels the first time an owner sees the pull
request, and when a role moves to a new owner, and reports a change only
after that, so a subscription to a pull request that is already `clean`
reports nothing until the label comes off. The daemon matches the labels
without regard to case, as GitHub does.

A Projector review is work only through its suggestions, and they stand
where `project review suggestions` lists them: in each account's newest
verdict on the current head by each model, and in the full review that an
incremental one builds on, which can be of an earlier head. Any other review
of an earlier head is history, so it reports nothing. The daemon announces
each review once. A pull request that a session on this machine reviews
waits for that review without a line of its own, and a draft waits on its
author.

A draft means its author is still working on the pull request, so a
repository subscription announces nothing for one: no `NEW PR`, `NEW HEAD`,
reminder, or `RESPONDED`. A review that stalls while it waits is still
reported once, as a `NEW HEAD` that says to stop the review's subagent and to
review the pull request once it is ready. Marking it ready announces what no
review has read:
`NEW PR` for a pull request never announced, `NEW HEAD` from the last head
known, or at once the `(still open)` reminder for a head announced before it
became a draft. A subscription to one pull request, as `review-pr` adds,
announces every head, draft or not, because running `review-pr` asks for its
review now.

`FINDING`, `VERDICT`, `CONFLICT`, `CHECKS`, and `NEW HEAD` report work that
stands until it is done, so the daemon announces each again, with
`(still open)` after the reference, every `watch.renotify` seconds while it
stands. A `NEW HEAD` stands while the head has no verdict by the model from
its reviewer and no review of it is in flight. After `MERGED`, `CLOSED`, or
`MISSING`, the daemon removes the pull request's subscription lines from
every session, and leaves the `!` lines.

### Inspect and stop the daemon

`list` prints each subscription with whether its session is live and whether
the session owns the role, as the daemon last recorded them. For a
repository's line, it prints the pull requests the session owns through it.
It lists the session's subscriptions, or every session's when no session is
named. With `--json`, it prints `subscriptions`.

`status` prints the daemon's pid, version, start time, and operator, the live
sessions, the pull requests and repositories it polls, its last pass, and the
GraphQL points it spent in the last hour. With `--json`, it prints the same
fields, and `daemon`, which is `running`, `started`, or `replaced`.

`stop` asks the running daemon to exit, waits up to 10 seconds, and prints
its pid, or says that none is running. The next `project watch` command
starts a new one. A command replaces a running daemon whose version is
strictly older than its own, so after `project upgrade` the new code takes
over at the first command. A daemon or a command whose version is `unknown`,
as one run from a checkout that was never installed reports, never replaces
and is never replaced. The daemon writes its output to `daemon.log` in the
state directory.

## Message other agents

`project agent` lets your agent sessions find each other and send each
other messages, in Claude Code and Codex alike. A session registers under a
name, a summary of what it is doing, or both. Another session lists the
registered sessions, sends one a message, and the watch daemon's delivery
wakes the recipient with one `PEER MESSAGE` line, as it delivers a pull
request event. On one machine this needs no setup. Across machines it needs
a key, as [Message agents on other machines](#message-agents-on-other-machines)
describes. The `message` skill tells an agent how to use these commands.

Each command acts for the session that `--session` names, then
`PROJECTOR_SESSION`, then `CLAUDE_CODE_SESSION_ID`, as the `project watch`
commands do, and takes `--json`. Only sessions signed in to the same GitHub
login see and message each other.

### Register a session

`register` records the session's name, summary, and model ID, and the GitHub
login, host, machine, and checkout it runs in:

```console
$ project agent register --name parser-refactor --summary "Refactoring the parser" --model claude-opus-5-5
registered parser-refactor (claude claude-opus-5-5) in acme/app feature; delivery: mod; queued for the relay
```

The line ends with `queued for the relay` when the relay runs for the
session, and otherwise with `local only:` and the reason.

- **`--name`** is 1 to 32 letters, digits, and hyphens, starting with a
  letter or digit. Names ignore case, and `register` stores them in
  lowercase. It refuses, with exit code 65, a name that another live
  session of the same login holds, on this machine or on another machine
  the relay has found, and names that session's summary. A lapsed
  session's name is free to take.
- **`--summary`** is one line of at most 120 characters.
- **`--model`** is the model ID the session runs: 1 to 100 letters, digits,
  and `.`, `_`, `:`, `/`, `@`, `[`, `]`, and `-`, starting with a letter or
  digit, such as `claude-opus-5-5` or `gpt-5.5`. Each message's event line
  prints the model before its authority notice, so `register` refuses, with
  exit code 2, a value with a space, a parenthesis, or any other character
  that could make it read as prose.

The first registration needs `--name`, `--summary`, or both. A later one
changes only the fields it gives, and records the checkout, the login, and
the host again. The host is `codex` while a Codex registration delivers the
session's events, `claude` when `CLAUDE_CODE_SESSION_ID` names the session,
and `other` otherwise.

Registering is how a session agrees to receive messages, so `register`
refuses, with exit code 65, a session that nothing delivers events to: one
whose delivery, as `project watch add` reports it, is `none`. It also
refuses when `gh` is not signed in, because it records the login `gh api
user` names. Every registration that succeeds starts the watch daemon when
none runs.

A registered session is live while its delivery is anything but `none`.
The watch daemon counts a live registered session as live, so an idle Codex
session with no subscriptions keeps the daemon running and still receives
messages.

### Registered when it subscribes

A session that subscribes to a pull request registers itself, so a loop that
fixes or reviews pull requests can receive messages without being asked to
register. When `project watch add` writes a subscription line, or finds it
there already, through a target, `--from-output`, or the Codex hook that
reads a push, and the session has no `agent.json`, `add` registers it the
way `register` does, with no name and this summary:

```console
$ project watch add acme/app#12
added acme/app#12 fix
events: mod
$ project agent list
local only: no agent.key is set; run `project agent key init` to make one
7c41d9e2b086 (you)	claude	laptop	acme/app feature	0s ago	Fixing acme/app#12
```

The summary says what the session reviews and fixes, such as `Reviewing
acme/app pull requests; fixing acme/app#12`. When the full list would pass
120 characters, it counts pull requests instead of naming them, as in
`Reviewing acme/app pull requests; fixing 39 pull requests`. It names only
the repository of the session's own checkout, because the relay publishes
the summary into that repository, which can be public. It counts any other,
as in `Fixing acme/app#12 and 1 pull request in another repository`. The
registration takes its model from a `review` subscription, when the session
has one.

While the summary is the generated one, Projector rewrites it each time the
session's subscriptions change: through `add`, `remove`, `drop`, and
`restore`, and when the daemon removes a merged, closed, or missing pull
request. With the relay on, the daemon publishes the new summary on its next
poll. Once the session gives its own summary with `register --summary`,
Projector never changes it again. A registration with `--name` alone keeps
the generated summary.

The registration is skipped, and `add` still succeeds with the same output,
when `agent.auto_register` is `false` in the checkout's configuration, when
the session unregistered, as
[Unregister a session](#unregister-a-session) describes, when nothing
delivers the session's events, when `gh` is not signed in, or when
any other step of the registration fails. A later `add` tries again, even
for a line the session has, so a session that nothing delivered to when it
first subscribed, such as one that starts `project watch stream` after `add`
prints `events: none`, registers on its next `add`. A running watch daemon
supplies the GitHub login, so the registration asks `gh` for it only when no
daemon has read it yet.

### Unregister a session

`unregister` stops a session that is still running from being reachable. It
removes the session's registration, so `list` no longer shows it, `send` and
`reply` to it refuse as they do for any session that is not registered, and
its name is free for another session to take:

```console
$ project agent unregister
unregistered parser-refactor; relay: 1 ref queued for deletion
```

Without it, a session leaves only by ending: it lapses, and the daemon's
cleanup removes its folder 7 days later. `unregister` also keeps a later
subscription from registering the session again. It leaves
`unregistered.json` in the session's folder, and while that file is there,
`project watch add` and the Codex hooks subscribe the session without
registering it, whatever `agent.auto_register` says. A later
`project agent register` registers the session again and removes the file.
That registration is a first one, so it needs `--name`, `--summary`, or
both.

Messages the session received before it unregistered stay in its folder,
and `read` still prints them until the daemon's cleanup removes the folder.
The session cannot `send` or `reply` while it is not registered.

The relay deletes the session's refs on its next pass, as it does for a
session that lapsed. `unregister` never pushes. It queues the deletion of
the session's presence ref, and of each message ref addressed to it that the
relay's cache knows of, in the repository's outbox, and starts the watch
daemon when none runs. The line ends with how many refs it queued, or
`relay: no refs to delete` when origin holds none. A message that another
machine sends before its next poll is deleted unread on the following pass.
Each other machine drops the session from its `list` once its next poll
finds the presence ref gone.

Unregistering a session that is not registered changes nothing in the
registry and exits 0:

```console
$ project agent unregister
this session is not registered; subscribing will not register it
```

With `--json`, `unregister` prints the `session`, whether it was
`registered`, its `name` or `null`, and `relay_refs_queued`, the number of
refs it queued for deletion.

### List the sessions

`list` prints the live registered sessions signed in to your login, one a
line, under a header that says whether the relay runs. Each line holds the
name, or its short ID when the session has no name, then the host and
model, the machine, the repository and branch, how long ago the record
changed, and the summary. `(you)` marks the session the command acts for:

```console
$ project agent list
relay: on, key 3f2a9c1d7e4b6a05 in the keychain
11d1a9295838	codex gpt-5.5	desktop	acme/app main	4m ago	Reviewing acme/app pull requests
a659f011e432	codex gpt-5.5	desktop	acme/app feature	4m ago	Writing the parser's tests
parser-refactor (you)	claude claude-opus-5-5	laptop	acme/app feature	1m ago	Refactoring the parser
```

A session's short ID is the last group of its session ID, so
`01a1e770-79df-7340-90dc-11d1a9295838` shows as `11d1a9295838`. A session
ID that is not a UUID shows as its last 12 characters. The last group of a
UUID is random, while a Codex session ID starts with the time the session
started, so two Codex sessions started within a minute of each other, such
as `01a1e770-79df-7340-90dc-11d1a9295838` and
`01a1e770-94fa-7cb0-99b6-a659f011e432`, share their first 8 characters and
differ in their last group. The short ID depends only on the session's own
ID, so `list`, the `PEER MESSAGE` line, `read`, and the output of `send` and
`reply` always show a session the same way, and `send` takes it in place of
the full ID.

The list holds this machine's sessions, then the sessions the relay found on
your other machines in the repositories this machine's live sessions work
in. When the relay does not run for the session, the header is `local only:`
and the reason: no `agent.key` is set, with the command that makes one, the
key could not be read, or the checkout has no GitHub origin. With an inline
key, the header says so and why, such as `relay: on, key 3f2a9c1d7e4b6a05
stored inline: secret-tool: not installed`. `unpublished` marks a session
whose current presence the relay has not pushed yet, such as one that still
waits in the outbox or a generated summary that changed since the last push.

`--all` adds lapsed sessions, marked `lapsed`. With `--json`, `list` prints
`local_only`, the `relay` header line, and `agents`, each session's record
with its `session`, `live`, and `you`. When the command acts for no
registered session, `list` reads the login from `gh`.

### Send, read, and answer a message

`send <to> <text>` sends a message to the session that `<to>` names, by its
name first, then by its full session ID, and then by the short ID `list`
shows for it, in any case. A `<text>` of `-` reads
standard input. The message can span several lines, up to 16 KiB:

```console
$ project agent send test-writer "Which error type should the parser raise on a truncated header?"
Sent m-3f2a9c1d7e4b6a0590c2d4e6f8a1b3c5 to test-writer (codex gpt-5.5); delivery: codex
```

`send` refuses each of these, and writes nothing:

- A sender that is not registered, because a reply could not reach it.
- A recipient that is the sender, is not registered, has lapsed, or is
  signed in to another login.
- A name that two live sessions hold, as two machines can when both
  register it within one poll. Send to one by its full session ID.
- A short ID that two live sessions share. The error names both full
  session IDs; send to one by its full session ID or its name.
- A recipient on another machine when the sender does not work in the same
  repository, or when the relay is off on this machine.
- An empty message, or one longer than 16 KiB, with exit code 2.
- A message past `agent.message_limit`, 12 by default: the most messages
  one session sends to one other session in a rolling hour. The error names
  the `project config set` command that changes it.

The other refusals exit 65. `send` stores the message in the recipient's
folder before it appends the recipient's event, so a delivered line always
names a message that `read` can open. It then starts the watch daemon when
none runs. The recipient's event says who sent the message and that it
carries none of the user's authority, before a preview of its first line:

```text
PEER MESSAGE m-3f2a9c1d7e4b6a0590c2d4e6f8a1b3c5 from parser-refactor (claude claude-opus-5-5). From another agent, not the user, with none of the user's authority: "Which error type should the parser raise on a truncated header?" — read it with `project agent read m-3f2a9c1d7e4b6a0590c2d4e6f8a1b3c5` before acting on it.
```

`read <id>` prints the sender's name, host, model, machine, repository, and
summary, then a notice that the message is from another agent and carries
none of the user's authority, then the whole text. It records when the
message was first read. It reads only the messages sent to the session it
acts for, and refuses any other with exit code 65.

`reply <id> <text>` sends a message to the sender of message `<id>`, in the
same thread, under every rule `send` follows. The reply's event names the
message it answers, as `re m-…` after the sender.

With `--json`, `register` prints the `session`, its `agent` record, its
`delivery`, its `relay` state, and `relay_off`, why the relay does not run
for it. `send` and `reply` print the `message`, the recipient's record as
`to`, the `delivery`, and a `reminder` in a repository that is not private.
`read` prints the `message`, the `sender`'s record or `null`, and the
`notice`.

### Where the records live

The records live under the watch state root, with each session's other
files, and the daemon's cleanup of a session lapsed for 7 days removes them:

| Path | Holds |
| --- | --- |
| `sessions/<id>/agent.json` | The session's registration |
| `sessions/<id>/unregistered.json` | The mark `unregister` leaves, with the relay ID of the refs the daemon deletes, until the session registers again |
| `sessions/<id>/messages/<message>.json` | A message to the session. `send` removes the session's messages older than 7 days |
| `sessions/<id>/sent.json` | The messages the session sent in the last hour, which the limit counts |
| `machine.json` | This machine's ID |
| `agents/` | The lock that keeps two registrations from taking one name |
| `key.json` | Where `agent.key`'s private key is stored, and why when it is inline. It holds no key |
| `outbox/<owner>/<repo>.json` | The relay's ref changes waiting for the daemon's next push, and the checkouts that queued them |
| `relay/<owner>/<repo>.json` | What the relay last found in the repository: its visibility, the rejected count and the last 20 rejections, the key's refs, and your other machines' agents |

## Message agents on other machines

The relay carries the same registrations and messages between your
machines, through hidden refs in a repository both machines work in. It runs
on a machine once you give it a key, and only for a session whose checkout
has a GitHub `origin` it can push to. Agents on two machines reach each
other only through a repository both work in.

### Make and copy the key

The key is an X25519 key pair. Make it once, on one machine:

```console
$ project agent key init
key 3f2a9c1d7e4b6a05 stored in the macOS Keychain; ~/.projector.toml names it as agent.key = "keychain:3f2a9c1d7e4b6a05"
To give another machine this key, run `project agent key join` there in a terminal.
```

`key init` stores the private key in the macOS Keychain, Windows Credential
Manager, or Linux `secret-tool`, and writes only its ID to
`~/.projector.toml` as `agent.key`. It sets the file's mode to `0600`. It
refuses when `agent.key` is already set; run `key remove` first to make a
new key, which no message sealed to the old key can be opened with.

Where no keychain answers, as on headless Linux, `key init` stores nothing
and names the keychain's error. Pass `--inline` to write the key itself
into `~/.projector.toml` instead, as `agent.key = "x25519:<base64>"`, where
every process running as you can read it. `key join` takes `--inline` too.

Copy the key to each of your other machines with `key join` and
`key grant`, both in a terminal. Nothing either one prints is secret, so
nothing you copy between the two terminals can leak the key:

1. On the new machine, run `project agent key join`. It makes a temporary
   key pair that lives only in its own memory, and prints a command to run
   on a machine that has the key, with a fingerprint:

   ```console
   $ project agent key join
   On a machine that has the relay key, run this in a terminal:

     project agent key grant pj1-laptop-Zx8k2Q...Qm9w

   Request fingerprint: K7Q-4MD-9XP
   Paste the line it prints here:
   ```

2. Paste that command into a terminal on a machine that has the key.
   `key grant` shows the request's machine and fingerprint; check that the
   fingerprint is the one `join` printed. On `y`, it encrypts the key to the
   request's temporary public key and prints one line:

   ```console
   $ project agent key grant pj1-laptop-Zx8k2Q...Qm9w
   Request from laptop, fingerprint K7Q-4MD-9XP.
   Encrypt key 3f2a9c1d7e4b6a05 to it? [y/N] y
   Paste this line into the waiting `project agent key join`:

     pk1-eyJ2IjoxLCJzZW5kZXIiOi...

   Key 3f2a9c1d7e4b6a05
   ```

3. Paste the line into the waiting `join`. It opens the line with the
   temporary private key, checks that the key the line carries is the key
   that encrypted it, and asks you to confirm the key ID that `grant`
   printed. On `y`, it stores the key as `key init` does:

   ```console
   Key 3f2a9c1d7e4b6a05. Does the other machine show the same key? [y/N] y
   key 3f2a9c1d7e4b6a05 stored in the macOS Keychain; ~/.projector.toml names it as agent.key = "keychain:3f2a9c1d7e4b6a05"
   ```

A session that registered before the machine had a key is published as
soon as the key arrives. Once `key init` or `key join` stores the key, it
encrypts and queues the presence of each live session registered on this
machine in a checkout with a GitHub `origin`, marks each `pending`, prints
how many it queued, and starts the watch daemon if none runs, so the daemon
pushes them within seconds. It skips a session that unregistered and one
whose checkout has no `origin`. With `--json`, `key init` lists the
sessions it queued as `queued`. The daemon also publishes them on its next
job for each repository when `agent.key` changes some other way.

`join` waits at most 30 minutes for the line, and Ctrl-C stops it at any
point. When it stops, times out, or refuses a line, it stores nothing, and
the temporary key goes with the process. It refuses a line encrypted to
another request, a line changed on the way, and any text other than a line
`key grant` prints. It refuses when `agent.key` is already set, unless you
pass `--replace`, which deletes the old key's keychain item once the new
key is stored.

Both commands refuse unless standard input and output are a terminal,
because an agent's command output lands in its transcript. `grant` encrypts
the key to any request it is given, so run it only for a request you made
with `join` and whose fingerprint matches.

| Command | What it does |
| --- | --- |
| `key show` | Prints the key's ID, its public key, and where it is stored |
| `key remove` | Deletes `agent.key` and the keychain item it names, which turns the relay off on this machine. The daemon then deletes this machine's agents' refs |

Projector reads `agent.key` only from `~/.projector.toml`. A checkout's
`.projector.toml` that sets it is ignored with a warning, so a repository
cannot plant a key its author knows. Projector refuses an inline key in a
file other accounts can read or change, and names the `chmod` that fixes it.
`project config set agent.key` refuses and names `key join`. Every other
command shows an inline key as `<redacted, key ID <id>>`, including
`config list` and `config get agent`, which print the table that holds it.
A `keychain:<id>` value holds no secret and prints as it is.

The keychain keeps the key out of `~/.projector.toml`, dotfiles
repositories, and backups. It does not stop a process running as you, such
as an agent that sets out to read the key, because the daemon reads it
without a prompt.

### How the relay carries a record

Commands never push. `register` and `send` seal their record and queue it in
the repository's outbox, then return. The watch daemon runs one job per
repository on a pool of two worker threads, so a slow `git` command never
holds up another session's events. On each pass, a job:

1. Fetches the key's refs, `refs/projector/agents/<key-id>/*` and
   `refs/projector/inbox/<key-id>/*`, with one `git fetch --prune` in a
   checkout of the repository.
2. Opens at most 64 new records. It counts a ref whose name does not match
   its namespace as rejected but leaves it unopened on `origin`, and rejects
   a commit whose tree is not exactly one `sealed.json` of at most 32 KiB
   before it reads the blob, and a record whose JSON nests more than 8 deep
   before it parses it. It checks the
   tag before it decrypts anything, and rejects a record sealed by any other
   key, a presence whose relay ID is not its ref's, a message whose
   recipient is not its ref's or that is 7 days old, and a record whose
   sender fields are not the shapes `register` gives them.
3. Delivers each message for a live session on this machine as a local
   message, once, and deletes its ref.
4. Renews each live agent's one-hour lease every 30 minutes, and deletes the
   refs of an agent that lapsed, unregistered, or whose key this machine no
   longer holds.
   On each full pass it also deletes presence whose lease expired and
   messages to an expired agent, whichever machine left them.
5. Writes every queued record as a parentless commit and pushes all of them
   in one `git push --porcelain`, with a `--force-with-lease` for each ref it
   moves or deletes.

The push runs the repository's pre-push hooks, once however many changes
are queued. It is killed after 15 minutes and a fetch after 2, with the
changes kept for the next pass. A change queued while a push runs starts
the next push as soon as that one ends, while a change whose push failed
waits for the next poll. Each commit names `projector
<projector@invalid>` as author and committer, at the Unix epoch, with the
message `projector relay` and no signature, so nothing about you or your
checkout reaches the repository outside the sealed blob.

Every record is encrypted with ChaCha20 and authenticated with HMAC-SHA256
under a key agreed with X25519, sealed to the key's own public key, so only
your machines can read or write your agents' records. A deleted ref's
commit stays readable by its SHA on GitHub, so a leaked key exposes every
record the relay pushed. In a repository that is not private, `send` prints
`public repository: name no private repository and describe none of its
internals`.

`project watch status` reports the relay:

```text
relay: on, key 3f2a9c1d7e4b6a05 (keychain)
relay acme/app: ok, 0 queued, 4 rejected (latest: tag mismatch, 04:37)
```

The first line says `relay: off` and why while the key cannot be read, such
as from a locked keychain. The daemon reads the key once when it starts, on
a worker thread, again when `agent.key` changes, and after a failed read on
each full pass. Each repository's line gives the last job's state, the
changes still queued, the records rejected so far with the reason and UTC
time of the latest, and any error, such as a push that timed out or
`no recorded checkout of the repository remains`.

A rejection can mean a forged or altered record, a record from a machine
with another key, or a bug in the relay's own checks, so the relay records
why. `project watch status --json` lists the last 20 rejections in each
repository under `relay.repositories.<owner/repo>.rejections`, oldest first,
beside the `rejected` count. Each entry holds only the `ref`, the `reason`,
and the time `at`, and never the record, its ciphertext, its plaintext, or
key material. The daemon also writes one line to `daemon.log` for each
rejection:

```text
2026-11-16T04:37:02+00:00 relay acme/app: rejected refs/projector/inbox/3f2a9c1d7e4b6a05/9bf078d49df0e5ff/m-2b…: tag-mismatch, at 2026-11-16T04:37:02+00:00
```

| `reason` | Status line | The relay rejected |
| --- | --- | --- |
| `ref-name` | ref name | A ref under the key's namespace whose name is not a presence or message ref. The relay counts it but leaves it on `origin`, since a later version of Projector could have written it |
| `tree-shape` | tree shape | A commit whose tree is not exactly one `sealed.json` |
| `too-large` | too large | A `sealed.json`, or the record inside it, larger than 32 KiB |
| `unreadable` | unreadable blob | A `sealed.json` that Git could not read |
| `too-deep` | JSON too deep | JSON nested more than 8 deep |
| `not-json` | not JSON | A `sealed.json`, or the record inside it, that is not JSON |
| `not-sealed` | not a sealed record | JSON that is not a sealed record of version 1 |
| `tag-mismatch` | tag mismatch | A record whose tag does not match: altered, or sealed to another key |
| `other-key` | sealed by another key | A record that opens with this key but was sealed by another |
| `relay-id-mismatch` | relay ID mismatch | A presence whose relay ID is not its ref's |
| `recipient-mismatch` | recipient mismatch | A message whose recipient is not its ref's |
| `message-id-mismatch` | message ID mismatch | A message whose ID is not its ref's |
| `field-shape` | field shape | A record with a missing field or a field that is not the shape `register` or `send` gives it |
| `expired` | expired | A message 7 days old |
| `collision` | ID collision | A message under the ID of a different message already delivered |

## Change a loop's tracked file

A watch or review loop reads its tracked file, one `owner/repo#number` a
line, and acts only on the pull requests the file lists. A line
`!owner/repo#number` keeps that pull request out of the loop wherever it sits
in the file, and both watchers and Projector's panel match it without regard
to case. Change the file with `project loop`, whose four commands each
change one kind of line:

| Command | What it changes | When to run it |
| --- | --- | --- |
| `add` | Appends an `owner/repo#number` line, and creates the file. | To bring a pull request into the loop. |
| `remove` | Removes the lines that list the pull request, and leaves its `!` lines. | To forget a pull request that merged or closed. |
| `drop` | Appends a `!owner/repo#number` line. | To keep an open pull request out of the loop. |
| `restore` | Removes the pull request's `!` lines. | To let a dropped pull request back in. |

```console
$ project loop add acme/app#337 --tracked ~/.local/state/projector/projector-review-loop.tracked
added acme/app#337 to /home/ada/.local/state/projector/projector-review-loop.tracked
$ project loop drop acme/app#337 --tracked ~/.local/state/projector/projector-review-loop.tracked
dropped acme/app#337 from /home/ada/.local/state/projector/projector-review-loop.tracked
$ project loop restore acme/app#337 --tracked ~/.local/state/projector/projector-review-loop.tracked
restored acme/app#337 to /home/ada/.local/state/projector/projector-review-loop.tracked
$ project loop remove acme/app#337 --tracked ~/.local/state/projector/projector-review-loop.tracked
removed acme/app#337 from /home/ada/.local/state/projector/projector-review-loop.tracked
```

Each command matches the reference without regard to case.

`add` and `drop` append their line rather than rewriting the file, because a
watcher may be appending a pull request it adopted at the same moment. `add`
creates the tracked file and any missing directory above it, so a loop
builds its set from nothing by adding each pull request. It changes nothing
when the file already lists the pull request. It refuses, with exit code 65,
a pull request the file drops, because the `!` line would keep that pull
request out all the same; run `project loop restore` first to let it back
in. `drop` changes nothing when the file already holds an exclusion for the
pull request.

`restore` removes every `!` line for the pull request, and only those, by
writing the file anew and renaming it into place. It leaves a plain
`owner/repo#number` line alone, so the pull request returns to the loop when
the file lists it.

`remove` removes every plain line for the pull request by the same rewrite,
and changes nothing when the file does not list it. A loop runs it when a
pull request merges or closes. It leaves the pull request's `!` lines alone,
so a pull request you dropped stays out even if it reopens and a review loop
would otherwise adopt it again.

Each command refuses, with exit code 65, a reference that is not
`owner/repo#number`. `drop`, `restore`, and `remove` refuse, with exit code
66, a tracked file that does not exist. With `--json`, each prints its
`action`, which is `added`, `dropped`, `restored`, `removed`, or
`unchanged`, with the `pr` and the `tracked` file.

## Put an iOS app on a device

`project ios` builds a branch or a pull request of the repository's iOS app
without touching your checkout, puts it on your iPhone, iPad, or Mac, and
brings that device's crash reports and log back. It needs Xcode's
command-line tools on macOS, and exits 69 without them.

```sh
project ios build 12                  # pull request 12, installed and opened on the connected device
project ios build feature/login       # a branch
project ios build 12 --no-install     # build only
project ios install 12                # put pull request 12's kept build on the device again
project ios devices                   # paired devices, and which can take an install
project ios crashes                   # the app's newest crash reports, copied and summarized
project ios logs --last 20m           # the app's log lines from the last 20 minutes, to a file
project ios cleanup                   # remove the worktree, DerivedData, and kept builds
```

`build` fetches the branch, or the pull request's head, into one detached
worktree that Projector keeps for the repository, and builds it into one
shared DerivedData folder, so moving between branches rebuilds only what
changed. It keeps a copy of each target's app, so `install 12` installs the
build of pull request 12 even after you build another target. With no target,
`install` installs the newest kept build. A pull request's head from a fork,
or one with a commit by an author who has no write access to the repository,
is refused with exit 65, because `xcodebuild` runs the project's build
scripts as you. Read its build phases and scripts, then pass
`--allow-untrusted` to build it. `build`, `install`, and `cleanup` take
turns: while one runs in a repository, another of the three waits for it to
finish. `devices`, `crashes`, and `logs` never wait.

Each of `build`, `install`, `crashes`, and `logs` takes these options:

| Option | Effect |
| --- | --- |
| `--app <app>` | Uses that app instead of the default one. |
| `--device <name-or-UDID>` | Uses that paired device. |
| `--iphone`, `--ipad` | Uses the one connected iPhone, or the one connected iPad. |
| `--mac` | Builds the app as Designed for iPad and opens it on this Mac, quitting the copy that is running. `crashes` and `logs` read this Mac instead of a device. |

Without a device option, the commands use the one device that is connected,
and refuse none or several with the list of paired devices. `build` checks
the device before it builds, and waits up to five minutes for you to unlock
a locked device before it opens the app. When `devicectl` times out waiting
for CoreDevice, which can happen right after you log in, update Xcode, or
connect a device for the first time, the error says to run the command again
in a minute, and to run `killall CoreDeviceService` if it keeps timing out.
launchd starts the service again the next time `devicectl` needs it.
Outside a terminal, `build` prints only the line that starts the build and
the line that ends it, and a failed build prints its `error:` lines.
`--verbose` prints every line, and `build.log` keeps them all.

### Find the app by convention

A repository that follows the convention needs no configuration:

- **The Xcode project** is the one `.xcworkspace`, or with none the one
  `.xcodeproj`, in the repository. Set `ios.project` to the path of another.
- **The app** is the Xcode project's name, so `MyApp.xcodeproj` gives
  `MyApp`. Pass `--app`, or set `ios.app`, for another. Set `ios.app` in a
  project whose Development schemes build several apps, which `project check`
  warns about.
- **The scheme** is `<app> Development` when the project has it, and
  otherwise `<app>`. A scheme named `MyApp Beta` is the app `MyApp Beta`.
- **The output folder** for crash reports and log files is `~/Desktop`. Set
  `ios.output` to move it.

`crashes` copies the app's three newest crash reports to the output folder,
keeping a copy already there, and prints when each happened, which build it
was, the exception or the termination code, and the top of the thread that
crashed. A watchdog kill crashes no thread, so the summary shows the main
thread. `logs` writes the app's lines to a new file named like
`MyApp log 2026-11-15 at 14.42.10.txt`, which never replaces an earlier one.
The lines are those of the app's subsystem, which is its bundle ID or the
bundle ID without a last part such as `.dev`, and the faults its process
writes. Pass `--predicate` to choose the lines yourself. On a device, `logs`
runs `sudo log collect`, which asks for your password and can take a few
minutes, and deletes the archive it reads.

### Know where the state lives

`project ios` keeps each repository's state in
`~/.local/state/projector/ios/<owner>/<repo>/`, or under
`$XDG_STATE_HOME/projector/` when it is set:

| Path | Holds |
| --- | --- |
| `worktree/` | The detached worktree. A build discards any change made in it. |
| `DerivedData/` | The shared build folder. |
| `build.log` | The newest build's whole log. |
| `builds/<app>/<key>/` | One target's kept app and its `build.json`. The key is `pr-<number>` for a pull request, and `branch-` with the branch name percent-encoded for a branch. |
| `lock` | The file a run holds while it works. `cleanup` keeps it. |

`devices --json` prints `devices`, each with its `name`, `udid`, `kind`,
`tunnel`, and whether it is `installable`.

## Find a branch's worktree

`project worktree` prints the folder of the worktree that has a branch
checked out. A worktree's folder is often named for something other than
its branch, such as an agent's id, so the command saves you from reading
`git worktree list` to find it. Give it a branch, or the number of a pull
request of the repository `origin` names, which it looks up on GitHub for
the pull request's head branch:

```sh
project worktree fix/login           # the worktree that has fix/login checked out
project worktree 12                  # the worktree that has pull request 12's head branch checked out
cd "$(project worktree 12)"
```

The command searches every worktree of the checkout you run it in, the main
checkout included, from any one of them. It matches the whole branch name,
and passes over a worktree whose folder is gone, locked or not. When no
worktree has the branch checked out, it says which branch it looked for and
exits 66. A detached worktree, such as a review's scratch worktree, has no
branch, so the command never finds one. A pull request from a fork has a head
branch in the fork, and the command looks for a local branch of the same
name, so for a fork's `main` it finds the worktree that has your own `main`
checked out. The command needs Git 2.36 or later, which added
`git worktree list -z`.

## Consume JSON

Pass `--json` to `init`, `list`, `show`, `search`, `create`, `status`,
`priority`, `done`, `check`, any `config`, `inbox`, or `loop` command, or
`watch add`, `remove`, `drop`, `restore`, `list`, `take`, or `status`, and to
`ios devices`. Responses use stdout only for JSON and include
`"schema_version": 2`. An `inbox` command that prints one item prints it under
`item`, and `inbox list` prints `items`. `inbox watch --json` prints one JSON
object a line, as a watcher reads it.

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
| `65` | Project data is invalid, a mutation is unsafe, an inbox item, answer, or snooze is refused, a `loop add`, `loop drop`, `loop restore`, or `loop remove` reference is not `owner/repo#number`, or `loop add` names a pull request the tracked file drops, `watch add` names a pull request the session drops from the role, or `watch drop` names a pull request the session does not review. |
| `66` | The requested project, the projects directory, the inbox item, or the tracked file of a `loop drop`, `loop restore`, or `loop remove` command does not exist. |
| `67` | The requested project is ambiguous, or an inbox reference names items in more than one session. |
| `69` | Git, the interactive editor, or the installer is unavailable. |
| `75` | Another command held an inbox or watch session's lock for five seconds; run the command again. |
| `78` | A `.projector.toml` file is not valid TOML. |
| `141` | The reader of the command's output closed before reading all of it, as `head` does once it has its lines. The command stops without a message, and 141 is the status a shell reports for `cat` or `git` stopped the same way. |
