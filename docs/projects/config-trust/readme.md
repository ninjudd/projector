---
status: ready
priority: now
---

# Read committed configuration from the default branch, and let outer files override it

A `.projector.toml` that Git tracks takes effect from the repository's default
branch, never from whatever branch is checked out. A file outside the
repository can override a setting the repository defines, with an
`[override.<priority>]` table.

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
put them there. Any file may also hold an `[override.<priority>]` table, whose
settings beat every ordinary setting on the walk. Among overrides the higher
priority wins, and at equal priority the farthest file wins, with
`~/.projector.toml` farthest of all. Two smaller
guards close the cases the default branch cannot. A **destination** setting,
one that says where your data goes, needs a one-time
`project config allow` when its value comes from a tracked file, because the
default branch's owner may not be you. And `projects.dir` and `ios.output`
must resolve inside the repository unless `~/.projector.toml` sets them.

### 2.1 Runtime flow

1. **Find the repository.** `config.load(start)` finds the Git repository that
   holds `start`: its working tree's top, its common Git directory, and the
   default branch's commit. That commit is the newer of two: the one
   `refs/remotes/origin/HEAD` names, which your own fetches move, and the one
   in `refs/projector/default/origin/<branch>`, which the watch daemon's fetch
   moves (step 9). The newer is the one that descends from the other. When
   neither descends from the other, as after a force-push to the default
   branch, Projector takes yours. It reads these from the `.git`
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
4. **Apply overrides by priority.** A file overrides settings in an
   `[override.<priority>]` table, where the priority is a whole number from 1
   to 99. The table holds settings under their usual names:

   ```toml
   # ~/work/.projector.toml, for a company
   [override.1]
   agent.relay = "github:acme/agents"

   # ~/work/acme/web-team/.projector.toml, for a team that needs its own relay
   [override.2]
   agent.relay = "github:acme/web-agents"
   review.gate_checks = "ci"
   ```

   After the ordinary merge, every override beats every ordinary value. Among
   overrides of one setting, the higher priority wins. At equal priority, the
   farthest file wins, and `~/.projector.toml` counts as farther than every
   walked file. So in the example, the team's relay wins because it chose 2.
   Had both chosen 1, the company's would win. An override of yours at any
   priority beats every other file's override at that priority, and you can
   always choose a higher one. A key under `override` that is not a whole
   number from 1 to 99 is a configuration error that names the file.
   `USER_ONLY` keys under an override are dropped from walked files, as they
   are elsewhere. `override` becomes a reserved name, and `SETTINGS` holds no
   key under it.
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
   in `$XDG_STATE_HOME/projector/allowed.json` on each machine, one per key
   and value. So a repository that changes the value asks again, while every
   other edit to its file, the same value set by another repository, and a
   return to a value you allowed before ask nothing.
   `project config allow --list` prints your approvals with the file each was
   granted for, and `project config allow --remove <key> <value>` revokes
   one, after which the next read waits for a new approval.
6. **Keep paths inside the repository.** A `projects.dir` from any file must
   resolve inside the working tree's top, or `project` refuses with the
   value and the file. An `ios.output` from any file but `~/.projector.toml`
   must resolve inside the working tree or under Projector's state
   directory.
7. **Write where it will take effect.** `project config set` and `unset`
   still change one file. When that file is tracked, they say
   `takes effect once this change merges to origin/main` beside the result.
   `--override <priority>` writes the key under `[override.<priority>]` in the
   chosen file. `config list` and `config get` show each value's source, such
   as `origin/main:.projector.toml`,
   `~/work/acme/.projector.toml (override 2)`, or `~/.projector.toml`.
8. **Cache.** Reading a tracked file from the default branch costs about 38
   ms with three Git commands. So Projector caches each blob in
   `$XDG_STATE_HOME/projector/config-cache/`, keyed by the default branch's
   commit and the file's path, and caches tracked-ness keyed by the index
   file's size and modification time. A load that hits the cache reads only
   files and starts no process.
9. **Keep the default branch fresh.** On each full pass, every
   `watch.full_every` seconds, 600 by default, the watch daemon fetches the
   default branch of each repository a live session works in, into a ref of
   Projector's own:

   ```console
   $ git fetch --quiet --no-write-fetch-head origin +refs/heads/main:refs/projector/default/origin/main
   ```

   It leaves `refs/remotes/origin/*`, your branches, and `git status` as they
   were, so a fetch you run yourself never fails with `cannot lock ref`, and
   nothing starts saying `behind by 3` on its own. Git's background
   maintenance fetches into `refs/prefetch/` for the same reason. The fetch
   runs on the relay's worker pool through `relay.git`, with a closed standard
   input, `GIT_TERMINAL_PROMPT=0`, and the fetch timeout, so it never prompts
   and never stalls the daemon. A fetch that fails leaves the last commit in
   place, with the error in `watch status`. A merged change to a tracked
   `.projector.toml` therefore takes effect within one full pass, and loading
   configuration still never touches the network. `watch.fetch_default =
   false` turns the fetch off. Without a running daemon, nothing fetches, and
   the default branch is as of your last fetch.

### 2.2 Data shapes

| Thing | Shape |
| --- | --- |
| `[override.<priority>]` | A table in any `.projector.toml`, keyed by a whole number from 1 to 99, holding settings under their usual dotted names. |
| `config.DESTINATIONS` | A frozenset of setting names, empty in this project. |
| `allowed.json` | `schema_version: 1` and `allowed`, a list of `{key, value, source, allowed_at}`. |
| `Config.sources` | Each value's source gains a form for a default-branch blob, `<branch>:<path>`, and an override flag. |
| `config-cache/` | One file per blob, named by the commit and a digest of the path, and `tracked.json`, keyed by the common directory and the index signature. |

### 2.3 Components

| File | Change |
| --- | --- |
| `src/projector/watch.py` | On each full pass, fetch each live repository's default branch into `refs/projector/default/origin/<branch>` on the relay's worker pool, unless `watch.fetch_default` is false, and report a failed fetch in `watch status` (§ 2.1 step 9). |
| `src/projector/config.py` | Add `watch.fetch_default` to `SETTINGS`. Find the repository (§ 2.1 step 1) and choose each file's source (step 2), with the cache (step 8). Merge ordinary values, then overrides (steps 3 and 4). Add `DESTINATIONS`, `NotAllowed`, and the check in `Config.setting` (step 5). Record each value's source and override flag. Reserve `override`. Update the module docstring, which describes the layers. |
| `src/projector/cli.py` | Add `project config allow <key> <value>`, terminal only, with `--list` and `--remove <key> <value>`. Add `--override` to `config set` and `unset`, the "takes effect once this change merges" note, and the sources and ignored-copy notes in `config list` and `config get`. |
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
  It works the way `!important` does in CSS.
- **Rank overrides by a number, and break ties by distance.** A single rule
  for which override wins would suit some organizations and not others: a
  company may need its relay to hold, while a team may need its own. A
  priority lets each file say how hard it insists, so neither case needs the
  other's file changed. At equal priority the farthest file wins, which is
  the simple default that the broader scope holds. Counting
  `~/.projector.toml` as farthest keeps you in charge of your machine
  without a special case.
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
| Set `review.gate_checks` in the repository, `[override.1]` for it in the grandparent and the parent directories, and in `~/.projector.toml`. Then raise the parent's to `[override.2]`. Then remove the user's and the parent's overrides. | First the user's override wins, then the parent's at priority 2, then the grandparent's. An ordinary parent value with no override still loses to the repository's. Catches priorities ignored, ties broken toward the nearer file, and an ordinary value that beats a nearer one. |
| Use `[override.0]`, `[override.100]`, and `[override.x]`. | Each is a configuration error naming the file. Catches a priority outside 1 to 99 read silently. |
| Put `agent.key` under `[override.1]` in the parent directory. | It is dropped with a warning. Catches a user-only key smuggled through an override. |
| Add a key to `DESTINATIONS` in the test, set it on the default branch, and read it. Then run `config allow` with and without a terminal. Then change the value on the default branch. | The read raises `NotAllowed` naming the file and the command. The allow refuses without a terminal and succeeds with one, and the new value asks again. The same value from the parent's untracked file needs no allowing. Catches a destination chosen by a branch's owner, and an approval that carries over to a new value. |
| Set `projects.dir = "../elsewhere"`, then `/tmp/x`, in the repository, and `ios.output = "/tmp/out"` in the repository and then in `~/.projector.toml`. | The `projects.dir` values are refused, the repository's `ios.output` is refused, and the user's is accepted. Catches writes outside the repository. |
| Run `config set review.gate x` on the repository's tracked file. | It writes the working tree and prints that the change takes effect once it merges to `origin/main`. Catches a write that silently does nothing. |
| Load configuration 100 times with a warm cache, with a fake `git` on `PATH` that records each run. | No load starts a process. Advancing `origin/HEAD` or touching the index makes the next load read Git once. Catches a cache that never hits, or one that never notices a new commit. |
| Merge a change to `.projector.toml` on `origin` without fetching, and run a full pass with a fake clock. Then fetch into `origin/main` yourself while the daemon's fetch runs, and force-push the default branch so the two refs diverge. Then set `watch.fetch_default = false`. | After the pass, the change applies, `refs/projector/default/origin/main` holds the merged commit, and `refs/remotes/origin/main` and `git status` are unchanged. Your own fetch succeeds. After the divergence, configuration reads your `origin/HEAD`. With the setting off, no fetch runs. Catches a fetch that disturbs your refs, a newer commit ignored, and an opt-out ignored. |
| Run `config allow --list`, then `config allow --remove` on an approved value, and read the setting again. | The list names each approval and its file. After the removal, the read raises `NotAllowed` again. Catches an approval that cannot be seen or taken back. |
| A repository with no `origin/HEAD`, and a start outside any repository. | Both read every file from disk, as today. Catches a load that fails where nothing can be trusted more than disk. |

Each test must fail when the fault it names is introduced, as
`skills/testing.md` asks.

## 5. Cost

Every `project` command loads configuration, and the panel runs several per
tick. With a warm cache a load adds a few `stat` and `read` calls. A cache
miss, after a fetch moves `origin/HEAD` or after the index changes, costs one
`git cat-file --batch` and one `git ls-files`, about 38 ms, once. Measure a
warm load in the tests' timing check, and compare `project list --json` on
this repository before and after the change. The daemon's fetch adds one
`git fetch` of the default branch per repository with a live session on each
full pass, which transfers only commits this clone lacks.

## 6. Open questions

- **A setting for one checkout only.** Deferred. Overrides already let a
  parent directory's file or `~/.projector.toml` beat a value the repository
  commits, which was the main reason for a personal file inside a checkout.
  What remains is scope: checkouts usually sit side by side under one
  parent, so a parent file reaches all of them, and a setting meant for one
  repository, such as turning off the default-branch fetch for a very large
  one, has no file of its own. Moving that checkout into a directory of its
  own works today. A `.projector.local.toml` that Git ignores, read from disk
  just above the repository's own file, could follow if that proves awkward.
- **Superprojects.** Deferred. A file tracked by a superproject that contains
  the repository as a submodule is read from disk, like any file another
  repository tracks.

## 7. Decisions

- **Overrides carry a priority, and the farthest file breaks ties.** Decided
  by the user on 2026-11-17. A file overrides in `[override.<priority>]`, the
  higher priority wins, and at equal priority the farthest file wins, with
  `~/.projector.toml` farthest of all (§ 2.1 step 4). A team can then beat a
  company by choosing a higher number, and a company's override holds when
  both choose the same one.
- **The watch daemon keeps the default branch fresh.** Decided by the user on
  2026-11-17. Each full pass fetches the default branch into Projector's own
  ref, and configuration reads the newer of that ref and your `origin/HEAD`
  (§ 2.1 steps 1 and 9). A merged policy change then reaches every machine
  with a running daemon within about 10 minutes, without disturbing your
  remote-tracking refs. `project config allow` can list and revoke approvals
  (§ 2.1 step 5).
