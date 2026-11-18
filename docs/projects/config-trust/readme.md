---
status: draft
priority: now
---

# Read committed configuration from the default branch, and let outer files override it

A `.projector.toml` that Git tracks takes effect from the repository's default
branch, never from whatever branch is checked out. A file outside the
repository can override a setting the repository defines, with an
`[override]` table.

## 1. Problem

Projector reads configuration from `~/.projector.toml` and from every
`.projector.toml` on the walk from the starting directory up to your home
directory, and the nearest file wins (`src/projector/config.py`). That lets a
repository commit its policy, such as `review.gate_checks`, and lets a parent
directory hold settings for every checkout under it. Two things go wrong.

**A branch can rewrite the policy you act under.** The walk reads each file's
working-tree copy. So any checkout whose working tree holds someone else's
version of the file applies their settings: after `gh pr checkout`, in a
worktree of a contributor's branch, or on a branch that has not been
reviewed. Reviewing a pull request does not apply its file, because
`project review gate` and the other review settings come from the checkout
you started the review in, not from the head's scratch worktree. But
everything else that runs in such a checkout does. These settings change
what a hostile file can do:

| Harm | Settings |
| --- | --- |
| Run a command on your machine | `review.gate`. `site.prepare` is already guarded by `--allow-prepare`. |
| Weaken a review's sign-off | `review.gate_checks`, `review.require_check`, `review.allow_approve`, `review.incremental`, `fix.resolve_human_threads` |
| Post as another of your accounts | `review.username`, which picks the `gh` login a review posts with. It does not feed the trusted-head check, which uses the authenticated login. |
| Write files outside the repository | `projects.dir`, which `core.py` resolves as given, absolute or with `..`, and `ios.output` |
| Send your data elsewhere | `agent.relay`, `agent.s3_endpoint`, and `agent.s3_profile`, planned in `../agent-messages/shared-relay/readme.md` and `../agent-messages/s3-relay/readme.md` |

**An outer file cannot overrule an inner one.** Nearest-wins means a
repository's committed value always beats a file above it. A team that keeps
`~/work/acme/.projector.toml` for its checkouts, or a person in
`~/.projector.toml`, cannot change a setting that a repository already sets.
For example, a team cannot route its agents through a team relay when a
repository names its own, without editing that repository.

## 2. Solution

Change where a tracked file's values come from, and add one way to overrule.
For each `.projector.toml` on the walk that Git tracks in the repository you
are working in, Projector reads the copy on the default branch,
`refs/remotes/origin/HEAD`, instead of the working tree. Edits on a branch
then take effect only once they merge, which is the review checkpoint the
repository already has. Untracked files, including every file above the
repository and `~/.projector.toml`, are read from disk as today, because you
put them there. Any file may also hold an `[override]` table, whose settings
beat every ordinary setting on the walk. Among overrides the nearest file
wins, and `~/.projector.toml`'s overrides beat every other file's. Two smaller
guards close the cases the default branch cannot. A **destination** setting,
one that says where your data goes, needs a one-time
`project config allow` when its value comes from a tracked file, because the
default branch's owner may not be you. And `projects.dir` and `ios.output`
must resolve inside the repository unless `~/.projector.toml` sets them.

### 2.1 Runtime flow

1. **Find the repository.** `config.load(start)` finds the Git repository that
   holds `start`: its working tree's top, its common Git directory, and the
   commit `refs/remotes/origin/HEAD` names. It reads these from the `.git`
   file or directory, `commondir`, the ref files, and `packed-refs`, as Git
   documents them, and falls back to `git rev-parse` when the layout is one it
   does not recognize. Every worktree of one repository shares the common
   directory, so a worktree under `.claude/worktrees/` and its main checkout
   are the same repository. A start outside any repository, or in one with
   no `origin/HEAD`, finds none.
2. **Choose each file's source.** For each walked file inside that
   repository's working trees, Projector takes the first of these that
   applies:
   - **The file is on the default branch.** Projector reads the blob at the
     file's path on that commit, whatever the working tree holds. When the
     working copy differs, `project config list` says
     `working copy ignored until merged`.
   - **Git tracks the file on this branch only.** A branch added it, so
     Projector ignores it, and `config list` says `not on origin/main yet`.
   - **Git does not track the file.** Projector reads it from disk.

   A file outside the repository, `~/.projector.toml`, and every file when no
   repository was found are read from disk. A file tracked by some other
   repository, such as a dotfiles checkout in your home directory, is read
   from disk too, because checking out a branch of the repository you work
   in cannot change it.
3. **Merge ordinary values.** The ordinary tables merge as today, from the
   user layer through the walk to the nearest file, and `USER_ONLY` keys are
   still dropped from every file but `~/.projector.toml`.
4. **Apply overrides.** Each file's `[override]` table holds settings under
   their usual names:

   ```toml
   # ~/work/acme/.projector.toml
   [override.agent]
   relay = "github:acme/agents"

   [override.review]
   gate_checks = "ci"
   ```

   After the ordinary merge, the walked files' overrides apply from the
   farthest to the nearest, then `~/.projector.toml`'s. So any override beats
   every ordinary value, a nearer override beats a farther one, and your own
   file has the last word. `USER_ONLY` keys under `[override]` are dropped from
   walked files, as they are elsewhere. `override` becomes a reserved name, and
   `SETTINGS` holds no key under it.
5. **Allow destinations from tracked files.** `config.DESTINATIONS` names the
   settings that say where your data goes. It starts empty, and the shared
   relay and S3 relay plans add `agent.relay`, `agent.s3_endpoint`, and
   `agent.s3_profile`. When a destination's effective value comes from a
   tracked file, read from the default branch, `Config.setting` returns it
   only once you have run `project config allow <key> <value>` in a terminal.
   The command refuses without a terminal, prints the file that sets the
   value, and asks for `y`. Until then, `setting` raises `NotAllowed`, which
   names the file and the command, and callers treat the setting as unset
   with that reason. A destination from an untracked file or
   `~/.projector.toml` needs no allowing, because you wrote it. Approvals live
   in `$XDG_STATE_HOME/projector/allowed.json`, one per key and value, so a
   new value asks again.
6. **Keep paths inside the repository.** A `projects.dir` from any file must
   resolve inside the working tree's top, or `project` refuses with the
   value and the file. An `ios.output` from any file but `~/.projector.toml`
   must resolve inside the working tree or under Projector's state
   directory.
7. **Write where it will take effect.** `project config set` and `unset`
   still change one file. When that file is tracked, they say
   `takes effect once this change merges to origin/main` beside the result.
   `--override` writes the key under `[override]` in the chosen file. `config
   list` and `config get` show each value's source, such as
   `origin/main:.projector.toml`, `~/work/acme/.projector.toml (override)`, or
   `~/.projector.toml`.
8. **Cache.** Reading a tracked file from the default branch costs about 38
   ms with three Git commands. So Projector caches each blob in
   `$XDG_STATE_HOME/projector/config-cache/`, keyed by the default branch's
   commit and the file's path, and caches tracked-ness keyed by the index
   file's size and modification time. A load that hits the cache reads only
   files and starts no process.

### 2.2 Data shapes

| Thing | Shape |
| --- | --- |
| `[override]` | A table in any `.projector.toml`, holding settings under their usual dotted names. |
| `config.DESTINATIONS` | A frozenset of setting names, empty in this project. |
| `allowed.json` | `schema_version: 1` and `allowed`, a list of `{key, value, source, allowed_at}`. |
| `Config.sources` | Each value's source gains a form for a default-branch blob, `<branch>:<path>`, and an override flag. |
| `config-cache/` | One file per blob, named by the commit and a digest of the path, and `tracked.json`, keyed by the common directory and the index signature. |

### 2.3 Components

| File | Change |
| --- | --- |
| `src/projector/config.py` | Find the repository (§ 2.1 step 1) and choose each file's source (step 2), with the cache (step 8). Merge ordinary values, then overrides (steps 3 and 4). Add `DESTINATIONS`, `NotAllowed`, and the check in `Config.setting` (step 5). Record each value's source and override flag. Reserve `override`. Update the module docstring, which describes the layers. |
| `src/projector/cli.py` | Add `project config allow <key> <value>`, terminal only. Add `--override` to `config set` and `unset`, the "takes effect once this change merges" note, and the sources and ignored-copy notes in `config list` and `config get`. |
| `src/projector/core.py` | Refuse a `projects.dir` that resolves outside the working tree (step 6). |
| `src/projector/ios.py` | Refuse an `ios.output` outside the working tree or the state directory unless `~/.projector.toml` sets it (step 6). |
| `docs/cli.md` | Rewrite "Read configuration" and "Change configuration" for the default-branch rule, `[override]`, `config allow`, and the path rules. |
| `tests/test_config.py`, `tests/test_projector.py`, `tests/test_ios.py` | The criteria in § 4. |

## 3. Why this design

- **Read committed policy from where it was reviewed.** A repository's
  `.projector.toml` is policy, and the default branch is where policy is
  reviewed and merged. Reading it there closes every route in § 1 that works
  by checking out a branch, with one rule rather than a list of sensitive
  keys to keep current as settings are added.
- **Trust what you put on disk.** An untracked file is one you or your team
  placed, and changing branches cannot rewrite it. Reading it from disk keeps
  a parent directory's settings and your own file immediate.
- **Overrule with an explicit table, not a new precedence rule.** Flipping
  the walk so outer files win would break repositories that rely on beating
  your personal defaults. An `[override]` table leaves ordinary settings
  exactly as they are, and says in the file that this value is meant to win.
  It works the way `!important` does in CSS. Your own file overriding last
  keeps you in charge of your machine.
- **Allow only destinations.** The default branch's owner may be a stranger
  in a repository you cloned. Most of their settings shape their own project,
  such as which checks gate a review, which you accepted by working there.
  A destination sends your data somewhere they chose, so it is the one kind
  that needs your consent. This is the rule `site.prepare` already follows
  for commands.
- **Read Git's files directly, and cache.** A configuration load runs on
  every `project` command, and the panel runs several each tick, so three Git
  processes per load would add about 38 ms to each. Git documents the
  layout of `.git`, refs, and `packed-refs`, and the cache makes the common
  case a few file reads.
- **Rejected: marking only sensitive keys as default-branch-only.** It is
  more precise, but it is a second rule to remember and a list that each new
  setting must remember to join.
- **Rejected: approving each file's content, as `direnv` does.** It asks
  again after every edit to a team's file, which trains people to approve
  without reading.
- **Rejected: user-only sensitive keys.** `review.gate_checks` and
  `review.gate` are team policy and must be committable.

## 4. Acceptance criteria

The tests build temporary repositories with a bare `origin`, a default
branch, a feature branch, a worktree under `.claude/worktrees/`, and
`.projector.toml` files in the repository, its parent directory, and a
temporary home.

| Exercise | Expected result, and the bug it catches |
| --- | --- |
| Commit `review.gate_checks = "ci"` on the default branch. On a feature branch, change it to `""` in the working tree and in a commit, and run `config get review.gate_checks`. | It prints `ci` from `origin/main:.projector.toml`, and `config list` notes the ignored working copy. Catches a branch that rewrites policy. |
| Add a tracked `.projector.toml` in a subdirectory on a feature branch only. | Its values do not apply, and `config list` says `not on origin/main yet`. Catches a file a branch adds. |
| Put an untracked `.projector.toml` in the repository's parent directory, and edit `~/.projector.toml`. | Both take effect at once from disk. Catches untracked files read from a branch. |
| From a worktree under `.claude/worktrees/`, whose main checkout is on another branch with a different working copy. | Both files read the default branch's blob, and the result matches a load from the main checkout. Catches a worktree that reads the main checkout's working copy. |
| Set `review.gate_checks` in the repository, `[override.review] gate_checks` in the parent, and another override in `~/.projector.toml`. Remove the user override and load again. | The user override wins, and then the parent's. An ordinary parent value without `[override]` still loses to the repository. Catches overrides applied in the wrong order, and an ordinary value that beats a nearer one. |
| Put `agent.key` under `[override.agent]` in the parent directory. | It is dropped with a warning. Catches a user-only key smuggled through an override. |
| Add a key to `DESTINATIONS` in the test, set it on the default branch, and read it. Then run `config allow` with and without a terminal. Then change the value on the default branch. | The read raises `NotAllowed` naming the file and the command. The allow refuses without a terminal and succeeds with one, and the new value asks again. The same value from the parent's untracked file needs no allowing. Catches a destination chosen by a branch's owner, and an approval that carries over to a new value. |
| Set `projects.dir = "../elsewhere"`, then `/tmp/x`, in the repository, and `ios.output = "/tmp/out"` in the repository and then in `~/.projector.toml`. | The `projects.dir` values are refused, the repository's `ios.output` is refused, and the user's is accepted. Catches writes outside the repository. |
| Run `config set review.gate x` on the repository's tracked file. | It writes the working tree and prints that the change takes effect once it merges to `origin/main`. Catches a write that silently does nothing. |
| Load configuration 100 times with a warm cache, with a fake `git` on `PATH` that records each run. | No load starts a process. Advancing `origin/HEAD` or touching the index makes the next load read Git once. Catches a cache that never hits, or one that never notices a new commit. |
| A repository with no `origin/HEAD`, and a start outside any repository. | Both read every file from disk, as today. Catches a load that fails where nothing can be trusted more than disk. |

Each test must fail when the fault it names is introduced, as
`skills/testing.md` asks.

## 5. Cost

Every `project` command loads configuration, and the panel runs several per
tick. With a warm cache a load adds a few `stat` and `read` calls. A cache
miss, after a fetch moves `origin/HEAD` or after the index changes, costs one
`git cat-file --batch` and one `git ls-files`, about 38 ms, once. Measure a
warm load in the tests' timing check, and compare `project list --json` on
this repository before and after the change.

## 6. Open questions

- **Precedence among overrides.** Needs the user's decision. Recommended:
  the nearest override wins, with `~/.projector.toml`'s last, so a team's
  override beats a company's and yours beats both. The alternative, the
  farthest override wins, lets a company's insist over a team's.
- **A personal file inside a checkout.** Deferred. A repository that tracks
  `.projector.toml` leaves no room for an untracked one beside it, so a
  person's per-checkout setting goes in a parent directory or in
  `~/.projector.toml`. A `.projector.local.toml` that Git ignores could
  follow if that proves awkward.
- **Superprojects.** Deferred. A file tracked by a superproject that contains
  the repository as a submodule is read from disk, like any file another
  repository tracks.
