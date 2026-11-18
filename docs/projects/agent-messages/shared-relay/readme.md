---
status: draft
priority: now
---

# Relay through a repository you choose

`agent.relay` in any `.projector.toml` names the repository the relay carries
records through. One relay can then serve one repository, a team, or a whole
company. With no `agent.relay`, the relay uses the checkout's own repository,
as it does today.

## 1. Problem

The agent-messages relay (`../readme.md` § 2.2) carries presence records and
messages as hidden refs in the repository a session works in, which
`review.origin_repo` reads from the checkout's `origin`. The daemon runs one
relay job per work repository, keyed by each registration's `repo`, and it
pushes from a live session's checkout. That choice made the relay work with
no setup, but it ties every link to one repository:

- **Your own agents.** Two of your agents on different machines reach each
  other only when they work in the same repository, so a session in an API
  repository cannot message one in its web client's repository.
- **Other people.** The peers plan (`../peers/readme.md`) scopes a person's
  inbox to one repository. A teammate's agent can reach you only through a
  repository you both work in, and an item it sends shows only in your
  sessions there.
- **The work repositories.** Every repository you work in collects
  `refs/projector/` refs and their leftover commits. Each relay push runs
  that repository's pre-push hooks, which in some repositories run a test
  suite.

## 2. Solution

Add the setting `agent.relay`, whose value is `github:<owner>/<repo>`. The
configuration walk already applies the nearest `.projector.toml` from the
checkout up to your home directory, so where you put the setting sets its
reach. A file in a repository covers that repository. A file in a parent
directory, such as `~/work/acme/.projector.toml`, covers every checkout under
it, for a team or a company. `~/.projector.toml` covers everything else. The
value `origin` means the checkout's own repository, which is also the
default, so a nested file can opt one repository back out. Each session
resolves its **relay location**, the repository slug its records travel
through, when it registers. The daemon runs one job per location rather than
per work repository. When no live session works in the location's own
repository, it runs the job's Git commands in a bare clone that it keeps under
the state root, which holds only `refs/projector/` refs. Records carry their
work repository, so everything that shared one repository now shares one
location: your agents' list, the peers inbox, and the rule for which sessions
can message each other. Three controls guard messages that cross
repositories, as § 2.3 describes. A location named outside
`~/.projector.toml` must be allowed once. A configured location must be a
private repository you can write to. And a record that crosses from a private
repository says so to its sender and its reader.

### 2.1 Runtime flow

1. **Configure.** You set `agent.relay = "github:acme/agents"` in the
   `.projector.toml` whose reach you want. The value is `origin` or
   `github:` and a repository slug. Any other value is a configuration error
   that names the forms. `s3://` is reserved for `../s3-relay/readme.md`.
2. **Resolve.** `project agent register` reads `agent.relay` from the
   configuration at the session's `cwd`. It records the resolved slug as
   `location` in `agent.json`, and the file that named it as
   `location_source`. With no value, or with `origin`, the location is the
   work repository and the source is `origin`. A checkout with no GitHub
   `origin` and no `agent.relay` keeps today's `relay: none`. One with a
   configured location relays through it.
3. **Allow a location a repository chose.** `agent.relay` is a destination
   setting, in `config.DESTINATIONS` (`../../config-trust/readme.md` § 2.1
   step 5). A tracked `.projector.toml` takes effect from the default branch,
   so a branch cannot choose your relay. When the effective value comes from
   such a file, it is used only once you run
   `project config allow agent.relay github:acme/agents` in a terminal,
   because the default branch's owner may not be you. A value from an
   untracked file, such as `~/work/acme/.projector.toml`, or from
   `~/.projector.toml` needs no allowing. Until a value is allowed, the
   registration records `relay: waiting`, and `project agent list` says
   `local only: github:acme/agents, set by origin/main:.projector.toml, waits
   for project config allow agent.relay github:acme/agents`. A location equal
   to the work repository needs no allowing, because the relay already pushes
   there today.
4. **Check the location.** For a location other than the work repository,
   the daemon reads `gh repo view <slug> --json visibility,viewerPermission`
   on first use and on each full pass, and records both in the location's
   cache. The relay is off for sessions using the location, with the reason
   in `list` and `watch status`, unless the repository is `PRIVATE` or
   `INTERNAL` and the permission is `WRITE`, `MAINTAIN`, or `ADMIN`. An
   `INTERNAL` repository is visible only within its GitHub enterprise, which
   suits a company relay. The work repository keeps today's rule, which
   allows any visibility.
5. **Run each location's job.** `Relay.repositories` becomes
   `Relay.locations`, which groups live registrations by `location`. The
   cache and outbox keep their paths, `relay/<owner>/<repo>.json` and
   `outbox/<owner>/<repo>.json`, now named by the location. A location that
   equals a work repository therefore keeps its files, and nothing migrates.
   `Job.checkout` still prefers a live session's checkout whose `origin` is
   the location. With none, it uses the bare clone in step 6.
6. **Keep a bare clone.** The daemon creates
   `<root>/relays/<owner>/<repo>.git` with `git init --bare` and sets its
   `origin` to `https://github.com/<owner>/<repo>.git`. Every relay Git
   command there runs through `relay.git` with `-c credential.helper=` and
   `-c credential.helper='!gh auth git-credential'`, so it authenticates as
   the `gh` login Projector already requires, whatever helpers your Git
   configures. The fetch names only `refs/projector/` refspecs, and Git's
   protocol v2 advertises only refs under those prefixes, so neither branches
   nor other people's refs are transferred. The bare clone has no hooks of
   its own, and a global `core.hooksPath` still applies.
7. **Carry the work repository.** Presence already holds `repo`. A message
   gains `repo` and `repo_visibility`, the sender's work repository and its
   visibility. A peers item gains the same two fields, and its `pr` may then
   name a pull request in that repository. The sender reads visibility with
   `gh repo view --json visibility`, cached for each repository in
   `<root>/visibility.json` and refreshed on each full pass. It writes
   `unknown` when `gh` cannot say.
8. **Share by location.** Every rule that compared work repositories now
   compares locations:
   - `project agent list` shows your live agents in every location this
     machine's live sessions use, with each agent's repository.
   - `send` reaches an agent on another machine when both sessions share a
     location. That replaces the same-repository rule in `../readme.md`
     § 2.1 step 4.
   - The peers inbox becomes the location's inbox. Each entry shows in every
     one of your sessions whose location matches, labeled with its work
     repository when that differs from the session's.
   - An agent can narrow one item to a repository. `inbox ask`, `request`,
     and `tell` with `--to` take `--repo <owner>/<repo>`, which may name the
     sender's own repository or another one. The sealed item carries it as
     `scope_repo`, and the recipient's machines enforce it. A scoped item shows
     only in the recipient's sessions whose work repository is `scope_repo`.
     So the panel there lists it, and agents there see it with
     `project inbox list`. Elsewhere, it still appears in
     `project inbox list --relay` and in `project console`, marked `for
     acme/web`, so an item for a repository the recipient is not working in
     never disappears. Replies to a scoped item are not scoped: they come back
     to the asker's sent copy, wherever that shows.

   Review coordination is about one repository, so `remote_reviews` still
   matches presence by `repo`.
9. **Guard crossings.** A record crosses when its work repository differs
   from the recipient session's, or when it goes to a peer. § 2.3 says what
   happens then.

### 2.2 Settings and records

| Setting or file | Meaning |
| --- | --- |
| `agent.relay` | `str`: `origin`, the default, or `github:<owner>/<repo>`. Read from every layer, nearest first. |
| `<root>/visibility.json` | `schema_version: 1` and `repos`, a map from repository slug to `{visibility, read_at}`. |
| `agent.json` | Adds `location` and `location_source`. `relay` gains the value `waiting`. |
| Message and peers item | Add `repo` and `repo_visibility`. The receiver checks both against `review.repo_slug`'s shape and the four visibility values `PUBLIC`, `INTERNAL`, `PRIVATE`, and `unknown`. |
| Peers item | Adds `scope_repo` (optional), the one repository whose sessions show it, in the same shape as `repo`. |
| `relay/<owner>/<repo>.json` | Adds `permission`, beside `visibility`, for a location other than a work repository. |

### 2.3 Cross-repository controls

| Control | What it stops |
| --- | --- |
| **Read a repository's choice of relay from its default branch, and allow it once.** | A branch, such as a contributor's pull request branch you checked out, choosing your relay, which config-trust prevents for every setting. And the owner of a repository you cloned choosing it, which the one-time `project config allow` prevents. A relay you can write to, chosen by someone else, would show its other readers your key ID, when your agents come and go, and how much they send, and your messages would not reach the relay your team watches. |
| **Require a configured location to be private or internal, and writable.** | A shared relay leaving permanent ciphertext, and the metadata above, on a public repository where anyone can fetch it, and a relay that fails on every push. |
| **Name the source of a crossing record.** | An agent copying a private repository's content into another repository without knowing where it came from. The `PEER MESSAGE` line, `project agent read`, and `project inbox show` name the sender's repository whenever it differs from the reader's. When `repo_visibility` is not `PUBLIC`, they add: `It comes from private repository acme/api: keep its content out of anything you write in acme/site unless that repository is private too.` |
| **Remind the sender.** | An agent sending private details to someone outside the repository without noticing. `send`, `reply`, and `inbox ask`, `request`, and `tell` with `--to` print `this leaves private repository acme/api: include only what the recipient may read` when the work repository is not `PUBLIC` and the record crosses. |

Nothing else limits which repositories may message each other within one
location. The file you put `agent.relay` in is the boundary: if you set it
for a company, the company's repositories share one relay.

### 2.4 Components

| File | Change |
| --- | --- |
| `src/projector/config.py` | Add `agent.relay` to `SETTINGS`, readable from every layer, and to `DESTINATIONS`, and `relay_location(value)`, which parses `origin` and `github:<owner>/<repo>` and refuses other forms, naming `s3://` as not yet supported. |
| `src/projector/relay.py` | Key `Job` and the daemon's `Relay` by location (§ 2.1 step 5). Add the bare clone and its credential arguments (step 6), and the location check with its cache fields (step 4). Pass the location, not the work repository, wherever a ref is queued. |
| `src/projector/agents.py` | Resolve and record `location` and `location_source` in `register`, and write `relay: waiting`. List and resolve recipients by location. Add `repo` and `repo_visibility` to messages, the crossing notice to `event_line` and `read_text`, and the send reminder. Read and cache visibility in `<root>/visibility.json`. |
| `src/projector/watch.py` | Report each location's state, permission, visibility, and the source file of a waiting location in `watch status`. |
| `src/projector/cli.py` | Add `project agent relay show [--json]`, which prints the location the current directory resolves to, the file that set it, whether it waits for `project config allow`, and the location's last state. |
| `src/projector/repo_inbox.py` (from `../peers/readme.md`) | Key the mirror by location. Add `repo`, `repo_visibility`, and `scope_repo` to items. Show the work repository on a crossing entry, and add the crossing notice and reminder. Filter a session's entries by `scope_repo`, and list every entry with `--relay`. |
| `src/projector/inbox.py`, `src/projector/cli.py`, `src/projector/console.py` | Add `--repo` to `inbox ask`, `request`, and `tell` with `--to`, and `--relay` to `inbox list`. The console lists scoped items in every session, marked with their repository. |
| `src/projector/permissions.py`, `tests/test_permissions.py` | Allow `project agent relay show` in the Codex rules. |
| `skills/message/SKILL.md` | Explain that agents and inbox entries are shared by relay location, and add the crossing rule. Never copy a crossing record's private content into a repository that is not private, and never run `project config allow`. |
| `docs/cli.md` | Document `agent.relay`, where to put it for a repository, a team, or a company, allowing a location, the bare clone, and the controls. |
| `tests/test_relay.py`, `tests/test_agents.py`, `tests/test_config.py` | The criteria in § 4. |

## 3. Why this design

- **Use the configuration walk as the scope.** The nearest-file rule already
  lets a repository, a directory of repositories, or your user file set a
  value, so placing `agent.relay` there gives a repository, a team, or a
  company relay with no new mechanism.
- **Name a location by its repository slug.** The default relay and a
  configured one are the same kind of thing, a GitHub repository, so every
  cache, outbox, and ref layout stays as it is. A session in the relay's own
  repository and a session elsewhere that names it share one job and one
  cache.
- **Push from a bare clone.** A shared relay repository is not something you
  check out, so the daemon keeps its own copy, which holds only relay refs.
  The `gh` credential helper makes it push as the account Projector already
  uses, with no SSH keys or helpers to set up. It also skips the work
  repository's pre-push hooks, which are there for code, not relay refs.
- **Leave trust in configuration to config-trust.** Reading `agent.relay`
  from walked files is the point of the feature, but a walked file may be one
  you did not write. Config-trust answers that for every setting at once: a
  tracked file counts only from the default branch, and a destination from
  one needs your consent. A relay-only allow list would solve half the
  problem in a second place.
- **Keep controls about information, not routing.** Sealing already limits
  who can read a record. The remaining risk is an agent carrying a private
  repository's content where it should not go, which only the agents writing
  and reading it can prevent. The notice and the reminder give both of them
  the fact they need: where the content came from, and whether that place is
  private.
- **Rejected: per-repository allow and deny lists.** They would repeat, in a
  second place, the boundary the configuration file already draws.
- **Refuse a public relay, not an internal one.** No team needs a public
  relay repository, and one would publish its metadata and ciphertext for
  good. An `INTERNAL` repository shows them only within the enterprise, which
  is the audience of a company relay.

## 4. Acceptance criteria

The relay tests' two-machine setup gains a third bare repository as the
shared relay, and parent directories holding `.projector.toml` files above the
temporary checkouts.

| Exercise | Expected result, and the bug it catches |
| --- | --- |
| Put `agent.relay = "github:acme/agents"` in a parent directory of checkouts of `acme/api` and `acme/web`, allow it, and register A in `api` on machine 1 and B in `web` on machine 2. Pass on both, and send from A to B. | Both list each other with their repositories, the message arrives, and every relay ref is on the shared relay and none on `api` or `web`. Catches a location ignored by any rule that still compares work repositories. |
| Put `agent.relay = "origin"` in `web`'s own `.projector.toml` under the same parent. | B relays through `web` again, and A can no longer reach it across machines. Catches a nearer file that cannot opt out. |
| Commit a location to the default branch's `.projector.toml`, and do not allow it. Then set another location on a feature branch only. | No ref is pushed anywhere, the registration says `waiting`, and `list` names the file and the `project config allow` command. The feature branch's location never applies. A location set in an untracked parent file or `~/.projector.toml` needs no allowing. Catches a repository or a branch choosing your relay. |
| Make the fake `gh` report the configured location `PUBLIC`, then `INTERNAL`, then `READ` permission. | The relay is off for `PUBLIC` and for `READ`, with each reason in `list` and `watch status`, and runs for `INTERNAL`. The default relay through a public work repository still runs. Catches a public or read-only shared relay, and a refused internal one. |
| Run a pass with only the bare clone available, with a global `core.hooksPath` hook that logs its runs and a fake `gh auth git-credential`. | The fetch and push run in `<root>/relays/acme/agents.git`, the hook logs one run, the fake helper is called, and the fetch's refspecs name only `refs/projector/` prefixes. Catches a clone that fetches branches, skips global hooks, or uses other credentials. |
| Send from a private `api` session to B in a public `web` session, and post a peers item from `api` to a peer. | `send` and `inbox ask --to` print the private-source reminder. B's line and `read` name `acme/api` and carry the private-source notice before the text. A message between two sessions in `api` carries neither. Catches a crossing that goes unmarked, and a notice on records that do not cross. |
| Relay a message whose `repo_visibility` is `PUBLIC` but whose sender's cache says `PRIVATE`. | The reader uses the record's value, and `read` shows no private notice. Catches a reader that consults a cache it may not have. |
| Register sessions in the relay's own repository and elsewhere on one machine. | One job runs for the location, from the checkout, and both sessions list each other. Catches two jobs racing on one location. |
| With existing caches and outboxes from a build without `agent.relay`, upgrade and pass. | Queued changes push, and no file moves. Catches a migration the design said was not needed. |
| On a team relay, post a peers item to a peer with `--repo acme/web` while the peer has live sessions in `acme/api` and `acme/web`. Then post one with `--repo acme/docs`, where the peer has no session. | The first shows only in the `web` session's list and panel, and `inbox list --relay` in the `api` session shows it marked `for acme/web`. The second shows in no session's list but appears in `--relay` and the console. A reply to either reaches the asker. Catches a scope ignored by the recipient, and a scoped item that vanishes. |

Each test must fail when the fault it names is introduced, as
`skills/testing.md` asks. After merge, set a team relay over two real
repositories on two machines, and exchange a message and a peers item across
them. Record the result in this plan.

## 5. Cost

- **Fetches.** One fetch per location per poll, rather than per work
  repository, so repositories that share a relay share a fetch. Protocol v2
  limits the advertisement to your key's prefixes, so a company relay with
  many people costs each fetch about what one repository does.
- **Pushes.** One push per location per pass, from the bare clone, with no
  repository pre-push hooks to run.
- **API.** One `gh repo view` per configured location and per crossing work
  repository on each full pass.
- **Disk.** One bare clone per configured location, holding only relay
  commits, a few hundred bytes each.

## 6. Open questions

These need the user's decision before the plan is `ready`. Each records the
recommendation the plan is written to.

- **Are the controls in § 2.3 enough?** Recommended: yes for this version,
  with no per-peer or per-repository lists until a team needs one.
- **A public relay on purpose.** Deferred. A setting could let you choose a
  public relay repository explicitly, if a case for one appears.

## 7. Decisions

- **The peers inbox is scoped by location.** Decided by the user on
  2026-11-17. An item addressed to you shows in every one of your sessions
  that shares its relay location, labeled with its work repository when that
  differs from the session's (§ 2.1 step 8). With a team relay, the inbox is
  team-wide, which is what a team relay is for. With the default relay, it is
  still one repository's inbox.
- **An agent can scope one item to a repository.** Decided by the user on
  2026-11-17. The relay-wide inbox is the default, and `--repo` narrows one
  item to the sessions of one repository (§ 2.1 step 8), for a question that
  only makes sense where its subject lives.
- **A relay may be an internal repository.** Decided by the user on
  2026-11-17. A configured location must be `PRIVATE` or `INTERNAL`, and
  writable (§ 2.1 step 4). An explicit opt-in for a public relay can follow
  later (§ 6).
- **Trust in a relay setting follows config-trust.** Changed on 2026-11-17.
  The relay's own allow list is replaced by `../../config-trust/readme.md`: a
  tracked file's `agent.relay` comes from the default branch, and needs
  `project config allow` once (§ 2.1 step 3). Build config-trust first.
