---
status: in-progress
priority: now
---

# Watch pull requests from one on-demand daemon

One process per machine watches every pull request an agent session pushed
or is reviewing. It starts when a session needs it and stops when no
session does.

## 1. Problem

Projector drives a pull request to a clean review with two loops. Each is a
skill that keeps a watcher script running in a conversation and starts one
subagent per pull request. The operator is the GitHub account `gh` is signed
in as.

- **The watch loop**, `start-watch-loop`, runs in a session that opened a
  pull request. Its watcher, `watch-threads.sh`, reports findings, verdicts,
  conflicts, and review bodies, and a `fix-<n>` subagent runs `fix-pr` on
  each.
- **The review loop**, `start-review-loop`, runs once per repository. Its
  watcher, `watch-prs.sh`, reports each new head of the operator's pull
  requests and of those that request the operator's review, and a
  `review-<n>` subagent runs `review-pr` on each.

Three problems follow:

1. **Every pass spends GraphQL, and each pull request is polled two or three
   times.** A pull request a session opens is in both loops, and Projector's
   panel queries it a third time. GitHub gives an account 5,000 GraphQL
   points an hour, shared by every session and machine. At the default
   60-second interval with 10 pull requests, the two loops spend about 2,000
   of them whether or not anything changed. The limit is reached in
   practice: one session slowed its watcher to a 10-minute interval to cope,
   so findings took up to 10 minutes to surface.
2. **Somebody has to start each loop.** `implement` starts a watch loop only
   on a pull request it opens, and only from the top-level conversation, so
   a subagent hands its pull request up. A spec's pull request, or one pushed
   outside `implement`, has no watcher until the user runs
   `start-watch-loop`. Reviewing needs a session that the user keeps open
   for `start-review-loop`.
3. **Nothing keeps two review loops apart.** Two review loops on one model
   review every head twice. Only the user's care keeps a second one from
   starting on one machine, and a second machine already collides this way.

## 2. Solution

One daemon per machine, `project watch serve`, polls every pull request that
a live agent session subscribes to. Any `project watch` command starts it
when it is not running, and it exits a few minutes after the last session
goes away. A session subscribes to a pull request in the `fix` role when it
pushes one, whether the pull request holds a spec or an implementation, and
in the `review` role when it reviews one with `review-pr`. A new skill,
`review-repo`, subscribes a session to review every open pull request in a
repository that the operator wrote or that requests the operator's review,
including those opened later. Each pass asks GitHub a conditional REST
question per pull request, which costs nothing when nothing changed, and
runs GraphQL only for a pull request that changed, plus a full pass every
10 minutes. The daemon gives each event to one session per pull request and
role. In Claude Code, Projector's mod keeps the session's subscriptions
alive and delivers its events as prompts, so no watcher runs in the
conversation. On other hosts, `project watch stream` does both.
`start-watch-loop` and `start-review-loop` go away, and nobody starts a
loop.

### 2.1 The daemon

The daemon keeps its files under `$XDG_STATE_HOME/projector/watch/`, which
is `~/.local/state/projector/watch/` by default.

1. **Starting.** Every `project watch` command except `serve` and `stop`
   first tries the exclusive lock on `daemon.mutex` without waiting, through
   the portable `try_lock` in `src/projector/inbox.py`. When it gets the lock, no
   daemon is running: it lets the lock go and starts `project watch serve`
   detached, in a new session on POSIX and as a detached process on Windows,
   with its output appended to `daemon.log`. It does not wait for the daemon,
   because the daemon reads what the command wrote when it starts. Two
   commands that race each start one, and the second `serve` finds the lock
   held and exits.
2. **Running.** `serve` holds the lock for as long as it runs and writes
   `daemon.json` with its `pid`, `version`, `started_at`, and operator. Once
   a second it checks each session's folder for a changed file, which
   costs no request. It runs a pass every `watch.interval` seconds, and at
   once when a session subscribes to a pull request it has no result for.
3. **Replacing an older daemon.** A command replaces the daemon only when
   the version in `daemon.json` parses as dotted numbers and is strictly
   older than its own. It writes the daemon's pid to `stop`, waits up to 10
   seconds for the lock, and starts a new daemon. A daemon checks `stop` each
   second and exits when it names its pid. So after `project upgrade`, the
   first `project watch` command any session runs puts the new code in
   charge. A CLI run from a checkout that was never installed reports its
   version as `unknown`. It never replaces a daemon, and no command replaces
   a daemon it started, so two CLIs never replace each other's daemon in
   turn. `project watch stop` stops either.
4. **Stopping.** The daemon exits when no session has been live for 5
   minutes, when `stop` names it, and on a signal. The operating system
   drops the lock however the process ends, so a crash leaves nothing to
   clean up, and the next command starts a new daemon. While a Claude Code
   session is open, that command comes within a minute.
5. **Changing accounts.** The daemon reads the operator with `gh api user`
   when it starts and on each full pass. When the login changes, it drops
   every cached `ETag`, because GitHub varies an `ETag` with the token.

### 2.2 Sessions and subscriptions

A session is one agent conversation, with its subagents. A command finds its
session in `--session`, then in `PROJECTOR_SESSION`, as `project inbox`
does, and then in `CLAUDE_CODE_SESSION_ID`. Claude Code sets that variable
in every Bash command, a subagent's included, to the id that
`$.session.id()` gives the mod.

Each session has a folder, `sessions/<session>/`:

| File | Written by | Holds |
| --- | --- | --- |
| `subscriptions` | The CLI, under the folder's lock | One subscription a line, as below |
| `session.json` | The CLI | `deliverer`, which is `mod` or `stream`, and `renewed_at` |
| `events` | The daemon appends, and `project watch take` empties it | Event lines not yet delivered |
| `state.json` | The daemon | What it announced to this session and when, and the head it last saw for each review |

The folder's lock is the inbox's `locked`. A subscription line names a
target and a role:

```text
acme/app#12 fix
acme/app#13 fix auto
acme/app#14 review claude-opus-5-5 octocat
acme/app review claude-opus-5-5 octocat
!acme/app#9 review claude-opus-5-5 octocat
```

- **`owner/repo#number fix`** subscribes to the pull request's findings,
  verdicts, conflicts, review bodies, and merge or close.
- **`auto` at the end of a `fix` line** marks a subscription that a push
  added through `project watch add --from-output` (§ 2.3). It never
  outranks a subscription that existed before it (§ 2.5). An explicit
  `project watch add` of the same pull request drops the mark.
- **`owner/repo#number review <model> <reviewer>`** subscribes to its new
  heads and to the author's responses, for one model ID and the login its
  reviews post as. Sessions on two models each review.
- **`owner/repo review <model> <reviewer>`** subscribes to review every open
  pull request in the repository whose author is the operator or that
  requests the operator by login, including pull requests opened later.
- **`!` before a line** keeps that pull request out of that role for this
  session, wherever the line sits. The panel writes one when the user
  dismisses a pull request.

References match without regard to case. When the CLI writes a `review`
line, it resolves `<reviewer>` with `reviewer_login` from
`src/projector/review.py`: `--reviewer`, then `review.username`, then the
operator.

A session is live while its deliverer renews it. The mod renews at least
once a minute, and `project watch stream` on every turn of its loop. The
daemon notes, on its own clock, when it last saw each session's
`session.json` change, and a session unseen for 5 minutes lapses. The daemon
stops polling for a lapsed session and passes on what it owned, as § 2.5
describes. When one turn of the daemon's loop finds more than a minute gone,
the machine slept, and the daemon counts every session as seen at that
moment, so waking from sleep lapses no session. A lapsed session keeps its
folder. A resumed conversation renews it and is live again, and the daemon
announces what is outstanding for it. The daemon deletes a folder that has
been lapsed for 7 days.

A subscription ends when the session removes it, or when the pull request
merges or closes. The daemon announces `MERGED` or `CLOSED`, then removes
that pull request's lines from every session through the same code the CLI
uses.

### 2.3 What subscribes a session

Nobody starts a loop. Subscriptions follow from the work:

| When | Subscription | Written by |
| --- | --- | --- |
| A session opens or pushes to a pull request, a spec's or an implementation's | `fix` on it, and on every layer of a stack | The skill that pushed, `implement`, `spec`, or `fix-pr`, with `project watch add`. In Claude Code, the mod also passes the output of each Bash call that ran `git push`, `gh pr create`, or `gh stack` to `project watch add --from-output`. |
| `review-pr` reviews a pull request | `review` on it | `review-pr` |
| The user asks a session to review a repository's pull requests | `review` on the repository | `review-repo` |
| The user dismisses a pull request in the panel | `!` for each role the session holds | The panel, with `project watch drop` |
| The user asks a session to stop fixing or reviewing | The lines go | The skill, with `project watch remove` |

`project watch add --from-output` reads a command's output on standard
input. It finds each pull request URL, as `gh pr create` prints one, and
each `To <remote>` block with its `<old>..<new>  <source> -> <branch>`
lines, as `git push` prints them. For each pushed branch, it asks
`GET repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open`, one REST
request, and subscribes the session to `fix` on the open pull request it
finds. A branch with no open pull request subscribes nothing. So a push
subscribes the session that made it, inside a skill or not. The line it
writes ends with `auto`, so a session that pushes one fix to another
session's pull request does not take that pull request's `fix` role from
it.

`watch.auto_start = false` turns off the automatic `fix` subscriptions:
neither the skills nor the mod add one. `project watch add` still adds one
when asked directly.

A subagent's commands carry the top-level session's id, so a subagent that
opens a pull request subscribes the session it belongs to, and nothing hands
the pull request up.

### 2.4 One pass

1. **Gather the targets.** The daemon reads every live session's
   subscriptions. A pull request is a target when a live session subscribes
   to it, and a repository is a target when a live session subscribes to
   review it.
2. **Adopt.** For each repository target, the daemon sends a conditional
   request for each page of its open pull requests. On a changed page, each
   open pull request that the operator wrote or that requests the operator
   by login becomes a target. A failed request adopts nothing that pass.
3. **Ask the conditional questions.** For each target pull request, the
   daemon sends the requests in § 2.6, and one for the base branch of each
   pull request a session fixes.
4. **Pick the dirty pull requests.** A pull request is dirty when one of its
   requests answered `200` or failed, when its base moved and a session fixes
   it, when its last result left `mergeable` at `UNKNOWN`, when it has no
   result yet, or when its result is older than `watch.full_every`.
5. **Query each dirty pull request.** The daemon runs one GraphQL query, and
   reads the REST reviews when the reviews page changed or the full pass is
   due. It writes the result and the new `ETag`s to the cache. A failed
   query carries the last result forward.
6. **Route and announce.** For each pull request and role, the daemon finds
   the owner, as § 2.5 describes, works out what is outstanding for that
   role, and appends each new event and each reminder that is due to the
   owner's `events`.

The pass stays level-triggered. The conditional requests only decide which
pull requests to query sooner. Every pull request still gets a full query
within `watch.full_every`, and a request that fails leads to the query
rather than to silence.

### 2.5 One owner for each pull request and role

Two sessions can subscribe to one pull request in one role. For example, a
user runs `review-pr` in one session on a pull request that another
session's `review-repo` covers. The daemon gives each event to one of them,
the owner. A pull request has one owner for `fix`, and one owner for
`review` for each model.

1. **Candidates** are the live sessions with a subscription that covers the
   pull request in that role and no `!` line that excludes it. For `review`,
   a repository subscription covers each pull request it adopted.
2. **Precedence.** A subscription to the pull request beats a subscription
   to its repository. Among equals, the one added most recently wins. An
   `auto` line never outranks a subscription that existed when it was
   added, so only an explicit `project watch add` takes a role from another
   session.
3. **Moving.** Ownership stays until the owner lapses, removes the
   subscription, or is outranked by a new one. On a move, the new owner
   hears at once what is outstanding, and a former owner that is still live
   gets `MOVED`, which tells it to stop its subagent for that pull request.
   A `review` role moves only between reviews, as below.

A session that is not the owner hears nothing about the pull request in that
role.

A reviewer passes its session's id to `project review` as `--loop`, so each
session keeps its own record of published reviews, and the lock its review
holds names the session. The review's state and lock belong to the machine
and the model, not to the session: they sit under
`prs/<owner>/<repo>/<number>/<model-key>/` in the review state directory,
and `setup` refuses while a lock less than a day old holds the head. So the
daemon hands a review over only between reviews:

- **Waiting for a review in flight.** A review is in flight while a lock
  for the pull request and model, on any head, names the owner. When a
  newer subscription outranks a live owner with a review in flight, the
  move waits until that review publishes or is released, and the owner
  keeps the role meanwhile.
- **A stalled review.** Each review command touches the head's lock as it
  runs, so the lock's modification time is the review's last sign of
  progress. The daemon notes, on its own clock, when it last saw each lock
  change, and forgives a gap in its own loop as it does for a session, so a
  machine's sleep stalls no review. When a lock naming the owner has not
  changed for `watch.review_stall` seconds, 30 minutes by default, the
  review is stalled. When a session that outranks the owner waits for the
  role, the daemon sends the owner `MOVED`, clears the lock, copies the
  record as below, and moves the role. Otherwise the owner keeps the role,
  and the daemon clears the lock and announces `NEW HEAD` to it again,
  saying that its last review stalled (§ 2.7). A stall moves the role only
  to a session that outranks the owner, so the role never moves back and
  forth between two sessions.
- **A lapsed owner.** When the owner lapses, the daemon at once clears
  every lock on this machine that names the lapsed session, with no wait,
  and moves the role.
- **The record.** At every move, the daemon copies the pull request's
  entries from the former owner's record, `loops/<session>/published.json`
  in the review state directory, into the new owner's, and skips any
  already there. The new owner can then re-review with `--rereview` a head
  the former owner reviewed, and its next review can still be incremental.
  The daemon and `publish` both write a record under the inbox's `locked`
  on its folder, so a copy never loses an entry that a `publish` adds at the
  same moment.
- **The fence.** `publish`, `move`, and `settle` refuse when the head's lock
  does not name their `--loop`, and say that the review was handed over.
  The reviewer stops and reports, and does not run `setup` again. `publish`
  also writes its state and removes the lock only while the lock still
  names its `--loop`. So a stalled reviewer that wakes after the hand-over
  cannot publish over the new owner. A `publish` already past its lock
  check when the daemon clears the lock can still submit its verdict. The
  new owner's `publish` then finds that verdict outside its record, because
  the copy came first, and the second-verdict check refuses it and tells
  the reviewer to ask the user which review continues, as it does today.

On this machine, the new owner's `setup` replaces the former owner's start
comment without `--replace`, because this machine's review state records
it. `project review setup --replace <comment-id>` stays for a start comment
that a stopped session on another machine left.

Two machines each run a daemon. The guards on each head that exist today
keep their reviews apart: `project review setup` refuses while another
session's live start comment shows the same model reviewing the pull
request, on this machine or another, and `project review publish` refuses a
second verdict by the same model on one head that the loop's record lacks.

### 2.6 The conditional requests and the query

Each request sends the `ETag` its resource last answered with in an
`If-None-Match` header. GitHub documents that a `304` answer does not count
against the primary rate limit, and in a probe three `304` answers in a row
left `X-Ratelimit-Used` unchanged. In the table, *observed* means seen in a
probe of two open pull requests in this repository, and *inferred* means
read from the fields a response carries and not yet tested by triggering the
event.

| Request | Sent | Its `ETag` moves on | Status |
| --- | --- | --- | --- |
| `GET repos/{owner}/{repo}/pulls/{n}` | Per target pull request | A push (`head.sha`), a draft toggle (`draft`), a merge or close (`state`, `merged`), an issue comment (`comments`, `updated_at`), an inline comment or reply (`review_comments`), and a change in mergeability or CI (`mergeable`, `mergeable_state`) | The fields are observed. An issue comment moving `updated_at` is observed. The rest is inferred. |
| `GET repos/{owner}/{repo}/pulls/{n}/reviews?per_page=100&page=<p>`, where `<p>` is `floor(R / 100) + 1` for `R` reviews | Per target pull request | A new review, including a body-only `COMMENT` review, which no count on the pull request shows | Inferred |
| `GET repos/{owner}/{repo}/git/ref/heads/{base}` | Per base branch of a pull request a session fixes | A push to the base, which can bring a conflict while the head stands still | Inferred |
| `GET repos/{owner}/{repo}/pulls?state=open&per_page=100`, each page | Per repository target | A new pull request, a review request (`requested_reviewers`), and a push or draft toggle on any open pull request | The fields are observed. |

No request sees a thread resolved or reopened without a reply (inferred), so
that waits for the full pass. A reply moves `review_comments`, and `fix-pr`
always replies before it resolves.

`gh api` exits `1` on a `304`, as it does on a `404`, a server error, and a
network failure, so the daemon runs
`gh api -i -H 'If-None-Match: <etag>' <path>` and reads the status line
rather than the exit status:

| Status line | The daemon |
| --- | --- |
| `200` | Stores the new `ETag` and marks the pull request dirty. For the open-pulls list, it adopts from the body. |
| `304 Not Modified` | Changes nothing. |
| `404` for a pull request or a repository | Announces `MISSING` once and removes the line. |
| `404` for a base ref | Treats the base as gone, usually because the layer below merged and its branch was deleted. It queries the pull request once, which reads its new base. |
| Any other status, or none | Treats the request as failed and marks the pull request dirty, so the GraphQL query runs. |

The GraphQL query serves both roles and the panel:

```graphql
repository(owner: $o, name: $r) { visibility
  pullRequest(number: $n) {
    number title url body state isDraft reviewDecision mergeable updatedAt
    headRefOid headRefName baseRefName author { login }
    additions deletions changedFiles
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    comments(last: 100) { nodes { author { login } body } }
    reviews(last: 100) { nodes { author { login } authorAssociation submittedAt body } }
    changesRequested: reviews(states: [CHANGES_REQUESTED], last: 1) { nodes { author { login } body commit { oid } } }
    reviewThreads(first: 100) { nodes { id isResolved path line
      first: comments(first: 1) { nodes { databaseId body } }
      last: comments(last: 1) { nodes { databaseId author { login } body } } } } } }
```

`updatedAt`, `additions`, `deletions`, and `changedFiles` serve only the
panel's row, which shows the pull request's age and its changed lines, as
`FIELDS` in `hooks/github.ts` reads them today. They cost nothing, because a
scalar field adds no connection.

The daemon's query also asks for `nameWithOwner`, `mergedAt`,
`mergeCommit { oid }`, each issue comment's `databaseId`, `createdAt`, and
`updatedAt`, and the query's own `rateLimit { cost }`. `MERGED` names the
time and the merge commit, the check for another machine's live start
comment needs each comment's id and times, the daemon reads the review state
under the repository's own spelling, and `project watch status` reports the
points spent. The cache's `repository` object holds all of them.

The cache holds, under `cache/<owner>/<repo>/`, `pulls/<number>.json` with
each request's last `ETag` and the last result with its time,
`branches/<base>.json` with the branch name percent-encoded, and
`open/<page>.json`. Each write replaces the file whole.

### 2.7 Events

Each event line keeps the wording the watchers print today and ends with
what to do, after ` — `. A session that loaded no loop skill can act on it.

| Event | Role | Reports | The session |
| --- | --- | --- | --- |
| `FINDING` | `fix` | An unresolved inline thread | Runs `fix-pr` in the subagent `fix-<n>`, or `fix-stack-<n>` for a stack |
| `VERDICT` | `fix` | A real `CHANGES_REQUESTED`, including one whose findings are only in its body | The same |
| `CONFLICT` | `fix` | A branch that conflicts with its base | The same |
| `REVIEW` | `fix` | A review body with no inline thread, with the count of suggestions it lists | The same |
| `DRAFT` | `fix`, when the session does not also own `review` of it | No clean review has signed off the head. When no live session on this machine owns `review` of the pull request, the line says so. | Waits, or tells the user that no session reviews the pull request |
| `NEW PR` | `review` | A pull request this session now owns, with a head this model has not reviewed | Runs `review-pr` in `review-<n>`, passing the session's id as `--loop` |
| `NEW HEAD` | `review` | A head this model has not reviewed, or one whose review in this session stalled (§ 2.5) | The same. After a stall, it first stops the stalled subagent, which shares the session's `--loop` and so would pass the fence. |
| `RESPONDED` | `review` | The author answered every finding on an unmoved head | Runs `review-pr` with `--rereview` |
| `MOVED` | Any | Another session now owns the role. For `review`, that happens only while this session has no review in flight, or once its review stalled. | Stops its subagent for the pull request. For `review`, it runs no `project review finish`, because the review's state now belongs to the new owner. A reviewer that runs on is refused at its next `publish`, `move`, or `settle`, and stops. |
| `MERGED`, `CLOSED` | Any | The pull request merged or closed | Finishes its subagents, and runs `project review finish --force` for a reviewer |
| `MISSING` | Any | The pull request or repository does not exist or the operator cannot see it, and the daemon removed the line | Tells the user |

For example:

```text
CONFLICT acme/app#12 conflicts with main — run fix-pr on acme/app#12 in subagent fix-12
NEW HEAD acme/app#14 9f3c2a1 — run review-pr on acme/app#14 in subagent review-14 with --loop 08270a6c-e8dd-42c9-851b-ae1e507aa6dc
NEW HEAD acme/app#14 9f3c2a1, its last review stalled — stop subagent review-14, then run review-pr on acme/app#14 in subagent review-14 with --loop 08270a6c-e8dd-42c9-851b-ae1e507aa6dc
MOVED acme/app#14 review for claude-opus-5-5 to another session — stop subagent review-14 and run no project review finish
```

`FINDING`, `VERDICT`, `CONFLICT`, `DRAFT`, and `NEW HEAD` report work that
stands until it is done, and the daemon announces each again every
`watch.renotify` seconds while it stands. A `NEW HEAD` stands while the head
has no verdict by the owner's model from its reviewer and no review of it in
flight. On this machine, a review is in flight while a lock for the head
that has not stalled exists (§ 2.5). From another machine, it is in flight
while a live start comment of that model shows it, one that this machine's
review state does not record. So an event that never arrived comes back
with the next reminder, and a reviewer that died on this machine comes back
once its lock stalls.

### 2.8 Delivery

1. **Claude Code.** On `session.start`, the mod runs
   `project watch renew --deliverer mod`, which also starts the daemon. On
   each 5-second tick, it checks the session's `events` with `$.fs.stat`.
   When the file is not empty, it runs `project watch take --json`, which
   renews the session, and submits the lines as one prompt with
   `$.prompt.submit`, which waits until the session is idle, as an inbox
   answer does. When no event comes, it runs `renew` once a minute.
2. **Other hosts.** On a host without the mod, such as Codex, or in a Claude
   Code session with mods off, `project watch stream` renews the session
   every 30 seconds and prints each event as it arrives. It runs under the
   host's persistent process or monitor facility and does not exit on its
   own.
3. **Knowing which.** `project watch add` ends by saying how the session's
   events arrive: through the mod, through a running `stream`, or not at
   all, with the `stream` command to start. A skill starts `stream` only on
   that last answer, so one skill text serves both hosts.

`take` empties `events`, and nothing confirms the prompt arrived. A batch
lost in between, as when the mod reloads, comes back with the next reminder,
because every event that asks for work stands until the work is done.

### 2.9 Commands and settings

| Command | Does |
| --- | --- |
| `project watch add <target> [--role fix\|review] [--model <id>] [--reviewer <login>]` | Adds a subscription unless the session has it, and refuses one the session excludes. `<target>` is `owner/repo#number`, a pull request URL, or, for `review`, `owner/repo`. `--role` defaults to `fix`, and `review` needs `--model`. |
| `project watch add --from-output` | Subscribes to `fix` on each pull request that a command's output, read on standard input, shows it opened or pushed to |
| `project watch remove <target> [--role …]` | Removes the subscription and leaves exclusions |
| `project watch drop <target> [--role …]` and `restore` | Adds or removes the `!` line for a role |
| `project watch list [--json]` | Each subscription, whether the session is live, and whether it owns the role. Without a session, every session's. |
| `project watch take [--json]` | Prints and clears the session's events, and renews it |
| `project watch renew --deliverer mod\|stream` | Marks the session live |
| `project watch stream` | Renews and prints events until stopped |
| `project watch status [--json]` | The daemon's pid, version, live sessions, targets, last pass, and the GraphQL points it spent in the last hour |
| `project watch serve` | Runs the daemon. Every other command starts it, so a person never needs to. |
| `project watch stop` | Asks the daemon to exit |

Every command except `serve`, `status`, and `stop` takes `--session <id>`,
resolved as § 2.2 describes.

| Setting | Default | Meaning |
| --- | --- | --- |
| `watch.auto_start` | `true` | Whether a push subscribes the session to fix the pull request |
| `watch.interval` | `60`, at least `30` | Seconds between passes |
| `watch.full_every` | `600` | The longest a pull request goes without a GraphQL query |
| `watch.renotify` | `900` | Seconds before standing work is announced again |
| `watch.review_stall` | `1800` | Seconds a review's lock can go unchanged before the daemon treats the review as stalled (§ 2.5) |

The daemon reads `watch.interval`, `watch.full_every`, `watch.renotify`,
and `watch.review_stall` from the user's configuration, because one daemon
serves every repository.

`project loop add`, `drop`, `restore`, and `remove` stay unchanged for one
release, so a session that started a watcher before the upgrade can still
change its tracked file, and the release after removes them.

### 2.10 The skills

- **`review-repo`** is new and replaces `start-review-loop`. It subscribes
  the session to review the current repository with its model, runs
  `project review rules` once and warns as the review loop does today, and
  starts a `review-<n>` subagent on each `NEW PR`, `NEW HEAD`, and
  `RESPONDED`. Asked to stop, it removes the subscription.
- **`review-pr`** subscribes the session to review the pull request it
  reviews, passes the session's id as `--loop`, and stops on `MOVED`. When
  `publish`, `move`, or `settle` says the review was handed over, it stops
  and reports rather than running `setup` again.
- **`fix-pr`** subscribes the session to fix each pull request it pushes to.
  It takes from `start-watch-loop` the rules for acting on an event: one
  subagent per pull request, continued with a message while it runs, and the
  report of clean state and stack readiness.
- **`implement`** subscribes the session to fix each pull request and stack
  layer it opens, in place of starting a watch loop. Its rules to hand a
  pull request up from a subagent, to start no second loop, and to leave the
  review loop alone go. Its hand-over says whether the session is subscribed
  and how its events arrive.
- **`spec`** subscribes the session to fix a plan's pull request when it
  opens one.
- **`start-watch-loop`** and **`start-review-loop`** are removed, with
  `watch-threads.sh` and `watch-prs.sh`.

### 2.11 The panel

- **List subscriptions.** The panel lists the session's subscriptions from
  `sessions/<session>/subscriptions`, found through `$.session.id()`, rather
  than the tracked files of watcher commands it saw start. It still lists an
  earlier watcher's tracked file by its kind until that session ends.
- **Read the cache.** It reads each pull request's status from
  `cache/<owner>/<repo>/pulls/<number>.json` rather than its own GraphQL
  query every 30 seconds, and shows a pull request with no result yet as
  loading. It reads the operator from `daemon.json` in place of the query's
  `viewer { login }`, to sort the user's own pull requests apart. The query
  for an open drawer's threads stays.
- **Name the roles.** A pull request's drawer says `fixing` in the blue that
  `watching` uses today, `reviewing` in `claude` orange, or both.
- **Dismiss by role.** Dismissing a pull request runs `project watch drop`
  for each role the session holds.
- **Subscribe and deliver.** The mod passes Bash output to
  `--from-output` and delivers events, as § 2.3 and § 2.8 describe.

### 2.12 The files

| File | Change |
| --- | --- |
| `src/projector/watch.py` (new) | The daemon: sessions, subscriptions, owners, passes, conditional requests, the cache, and events |
| `src/projector/cli.py` | `project watch` and its subcommands. `run_review` passes the state directory, the model, and the loop to `census`, and the loop to `settle`, so both reach the lock. |
| `src/projector/permissions.py` | Codex's allow rule for whole `project` commands gains `watch`, so Codex runs the commands the skills run without asking |
| `src/projector/config.py` | `watch.interval`, `watch.full_every`, `watch.renotify`, `watch.review_stall`, and the new meaning of `watch.auto_start` |
| `src/projector/review.py` | `setup`, `census`, `settle`, `move`, and `publish` touch the head's lock as they run, and `census` finds this machine's review of the pull request by the model to do so. `publish`, `move`, and `settle` refuse a lock that does not name their `--loop`, and `publish` writes its state and removes the lock only while the lock still names it. `publish` writes the loop record under the inbox's `locked` on its folder (§ 2.5). `interdiff` touches the lock once, and `gate` touches it about once a minute while the repository's checks run, so a long gate never reads as a stall. `--loop` accepts every id `project inbox` accepts as a session. |
| `tests/test_config.py` | The documented settings may be whole numbers |
| `src/projector/loop.py` | Unchanged for one release, then removed with `project loop` |
| `skills/review-repo/SKILL.md` (new) | § 2.10 |
| `skills/review-pr/SKILL.md`, `skills/fix-pr/SKILL.md`, `skills/implement/SKILL.md`, `skills/spec/SKILL.md` | § 2.10 |
| `skills/start-watch-loop/`, `skills/start-review-loop/` | Removed, with both watcher scripts |
| `src/projector/templates/agents-block.md`, and this repository's `AGENTS.md` and `CLAUDE.md` through `project init` | `implement` subscribes the session rather than starting a watch loop, and a review rather than a review loop marks a pull request ready |
| `hooks/register.tsx`, `hooks/parse.ts`, `hooks/github.ts`, `hooks/types.d.ts` | § 2.11 |
| `hooks/parse.test.ts`, `hooks/panel.test.tsx` | Subscriptions, the cache, delivery, `--from-output`, and an earlier watcher's files |
| `tests/test_watch.py` (new) | The daemon, through a fake `gh` that prints status lines and honors `If-None-Match` |
| `tests/test_watchers.py` | Removed, its cases ported to `tests/test_watch.py` |
| `tests/test_packaging.py`, `tests/test_review.py` | The published skill set, and the tests that read the removed skills or scripts. In `tests/test_review.py`, the lock touch, the fence, and the record's lock. |
| `docs/cli.md`, `docs/plugins.md`, `README.md` | The commands, the settings, the skills, and the panel's roles |
| `web/components/sections/review-loop.tsx`, `web/lib/links.ts` | Name and link `review-repo` |
| `docs/projects/panel-themes/readme.md` | Its `watching` role becomes `fixing` |
| `web/components/site-footer.tsx` | Its links to the loop skills become links to `review-repo` and `fix-pr` |
| `tests/test_instructions.py` | Pins the agent instructions template's new version |

## 3. Why this design

- **One daemon per machine, not one per session, directory, or
  repository.** GitHub's budget is per account, and one repository is many
  directories once each session works in its own worktree. Subscriptions
  name `owner/repo`, so nothing depends on the directory, and one process
  sees every session's interest, so a pull request that two sessions share
  is polled once.
- **Started on demand, not resident.** The daemon holds no operating-system
  permission that would need a login agent under launchd or systemd, so
  nothing needs installing. A command starts it when nothing holds its lock,
  and it exits when idle, so nothing runs while no session cares, and a
  crash heals at the next command.
- **Files and a lock, not a socket.** The daemon answers no requests:
  commands write subscriptions, and the daemon writes events and results. So
  files carry everything, the inbox's lock already works on POSIX and
  Windows, and the panel already reads files with `$.fs`. The cost is up to
  a second before the daemon sees a new subscription.
- **Liveness by renewal, counted on the daemon's clock.** The mod runs on a
  timer and holds no long-lived process, so a held connection cannot show
  that a session is alive, but a renewal can. Counting on the daemon's own
  clock, and forgiving a gap in its own loop, keeps a laptop's sleep from
  lapsing every session at once, the trap an expiring lease falls into.
- **The session from the host's environment.** Claude Code gives every Bash
  command, a subagent's included, the session's id. A command therefore
  subscribes the right session with no argument, and the rule to hand a pull
  request up to the top-level conversation goes.
- **One owner by precedence, not a lease.** One process sees every
  subscription, so it chooses each owner on every pass. That removes the
  lease, its mutex, its holder record, the handover of tracked lines, and the
  loop id for each holding that separate watchers needed to share the review
  role. A subscription to a pull request outranks one to its repository
  because it is the narrower request, and the newest wins because it is the
  user's latest word. An `auto` line outranks no earlier one, because
  pushing one fix to a pull request is not a request to take it over.
- **The session's id as the review's loop id.** Each session's id differs,
  so the existing collision check tells two sessions apart. Copying the
  former owner's record at a move keeps that check from refusing the new
  owner's re-review of a head the former owner reviewed, and lets the new
  owner's next review be incremental.
- **A review handed over between reviews, with a backstop for a stall.** A
  review's state and lock belong to the machine and the model, and `setup`
  takes the lock before it reads `--replace`, so a role that moved in the
  middle of a review would leave the new owner refused for up to a day, and
  the former reviewer could publish over the new owner. Waiting for the
  review in flight avoids both, and the wait is short: reviews of this
  plan's pull request took 25 seconds to 7 minutes. A lock that no command
  touched for 30 minutes means the reviewer stopped, so the daemon takes the
  review back then rather than after a day. A lapsed session's reviewer is
  gone with its conversation, so its locks go at once. The fence stops a
  stalled reviewer that wakes after the hand-over, and the second-verdict
  check stops the one `publish` that the fence cannot.
- **Review only where the user asks.** The session that writes a pull
  request never reviews it unless the user asks it to. A host's automatic
  approval has refused a reviewer subagent's report to its main loop as
  self-approval, and that cannot happen here. The cost is that a pull
  request stays in draft while no session reviews it, and the `DRAFT`
  reminder says so.
- **Events that say what to do.** A session that only pushed has no loop
  skill loaded when an event arrives. An event that names its action lets
  that session act, and the finer rules sit in `fix-pr` and `review-pr`,
  which the action loads.
- **Pushes found in command output.** The mod sees every Bash call's output,
  so a push subscribes the session even outside a skill, as for a fix pushed
  by hand. The skills still subscribe themselves, so a host without the mod
  behaves the same.
- **A conditional request per pull request.** One request for a
  repository's open pull requests costs less, but it cannot see a conflict,
  which shows only in `mergeable` on a single pull request, and its `ETag`
  moves whenever any pull request in the repository changes. A `304` per
  pull request is just as free and names the pull request that changed. The
  list fits adoption, and it replaces the two `gh pr list` searches that
  cost 2 to 4 GraphQL points every pass.
- **Reviews and the base get requests of their own, and a full pass runs
  every 10 minutes.** A body-only review appears in no count on the pull
  request, and whether a conditional request makes GitHub recompute
  mergeability is untested, so the base's ref says when to look for a
  conflict. The full pass bounds the delay for what no request sees, such as
  a thread resolved without a reply, at 12 points an hour per pull request,
  and catches any inference in § 2.6 that proves wrong.
- **In the CLI.** Both hosts' skills already run the CLI. The daemon reuses
  `verdict_marker`, `HOLD_MARKER`, `model_key`, `reviewer_login`, and
  `authenticated_login` from `src/projector/review.py`, and `locked` and
  `try_lock` from `src/projector/inbox.py`, rather than copying patterns
  into bash. Replacing an older daemon, § 2.1, keeps a long-lived process
  from running code older than the CLI. Comparing only versions that parse
  keeps a checkout's `unknown` from replacing an installed daemon, and an
  installed CLI from replacing a checkout's, back and forth.
- **What is replaced.** Both loop skills and both watcher scripts go, and
  with them the tracked files a conversation chose, `--exit-when-closed` and
  `DONE`, because a subscription ends with its pull request, and `BRANCH`,
  because a push subscribes the session. `MOVED` replaces the lease's
  `ROLE`. The panel's own polling goes, except for the pull requests of a
  watcher started before the upgrade, which the daemon does not cache
  (§ 9.3). Every caller moves: `implement`,
  `fix-pr`, `review-pr`, `spec`, the agent instructions template, the panel,
  the docs, the homepage, and the tests that read the old files.
- **The name `review-repo`.** It pairs with `review-pr`: one reviews a pull
  request and the other every pull request of yours in a repository, and
  both keep reviewing new heads.
- **`watch.auto_start` keeps its name.** Renaming a setting would break every
  file that sets it.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Run `project watch add` with no daemon running | `daemon.json` names a new daemon within a second, and `project watch status` lists the subscription |
| Run two `project watch add` commands at once with no daemon running | One daemon runs, and the other `serve` exits |
| Kill the daemon, then run any `project watch` command | A new daemon starts |
| Let every session lapse | The daemon exits 5 minutes later |
| Run a command from a newer CLI against an older daemon | The older daemon exits, and a daemon of the newer version runs |
| Run a command from a CLI that reports `unknown` against a running daemon, and one from a released CLI against a daemon that reports `unknown` | Neither daemon is replaced |
| Stop renewing a session for 5 minutes, then renew it | It lapses, the daemon stops querying its pull requests, and on renewal it hears what is outstanding again |
| Pause the daemon for an hour, as a sleep does, while a review is in flight, then resume it | No session lapses, and no review stalls |
| Run two passes over a target whose requests answer `304` on the second | The second pass makes no `graphql` call |
| Answer `200` for one pull request's request on the second pass | One GraphQL query, for that pull request |
| Fail a request with a server error, a rate-limit `403`, or no status line | The GraphQL query runs for that pull request, and when it fails too, the last result carries forward |
| Answer `404` for a pull request | `MISSING` once, and the line is gone |
| Let `watch.full_every` pass while every request answers `304` | Each target gets one query |
| Leave `mergeable` at `UNKNOWN` | The pull request is queried each pass until it settles, and only `CONFLICTING` announces `CONFLICT` |
| Move the base of a pull request one session fixes and of one only reviewed | Only the fixed pull request is queried |
| Subscribe two sessions to one pull request, one in `fix` and one in `review`, and change it | One GraphQL query in all, `FINDING` to the first session, and `NEW HEAD` to the second |
| Subscribe session A to review a repository and session B to review one of its pull requests, on one model | B owns that pull request and A owns the rest. A, which owned it first, gets `MOVED`. |
| Lapse B | A owns the pull request again, and gets `NEW PR` only when its head has no verdict by this model |
| Subscribe session B to review a pull request while session A owns its review and has a review of it in flight | A keeps the role until its review publishes. Then B owns it, A gets `MOVED`, and B's record holds A's entries for the pull request. |
| Leave a lock naming the owner unchanged for 30 minutes while another session waits for the role | The owner gets `MOVED`, the lock is gone, and the new owner gets `NEW PR` |
| Leave a lock naming the owner unchanged for 30 minutes with no other candidate | The lock is gone, and the owner gets `NEW HEAD` saying its last review stalled |
| Run `publish` from the stalled reviewer after the hand-over | It refuses, saying the review was handed over, and the new owner's state, lock, and record are unchanged |
| Lapse an owner that holds a lock | The pass that finds it lapsed clears every lock on this machine naming that session, and the new owner gets `NEW PR` |
| After a move, answer every finding on a head the former owner reviewed | The new owner gets `RESPONDED`, and its `publish` under `--rereview` is accepted |
| Subscribe two sessions on different models to review one pull request, then push to it | Each gets `NEW PR`, then `NEW HEAD` |
| Under a repository subscription, open a pull request as the operator, request the operator's review on another, and request a team on a third | The first two are adopted and the third is not |
| Write `!acme/app#15 review …` in the subscribing session | That session hears nothing about review of #15 |
| Leave a head with no verdict and no review in flight past `watch.renotify` | `NEW HEAD` again |
| Merge a subscribed pull request | `MERGED` to each owner, and the pull request's lines are gone from every session |
| Pipe `git push` output for a branch with an open pull request, and `gh pr create` output, to `project watch add --from-output` | Each pull request gets a `fix auto` line, and a branch with no open pull request adds none |
| Pipe `git push` output from session B to `--from-output` for a pull request that session A fixes | B gets a `fix auto` line, and A keeps the `fix` role and gets no `MOVED` |
| Set `watch.auto_start = false` and push from Claude Code | No line is added |
| Run `project watch add` from a subagent in Claude Code | The line lands in the top-level session's folder |
| Put events in a Claude Code session's `events` | The mod submits them as one prompt, and `events` is empty |
| Run `project watch add` where the session has no deliverer | The output names the `stream` command to start |
| Open the panel in a session with subscriptions | It lists each pull request with `fixing`, `reviewing`, or both, and with its age and changed lines, from the cache, and sends no GraphQL query of its own |
| Run the daemon for an hour on a live repository with `N` quiet pull requests | `gh api rate_limit` shows the GraphQL count growing by about `12N` an hour, and not between full passes |

These tests fail before the change: `tests/test_watch.py`, the subscription,
cache, and delivery cases in `hooks/parse.test.ts` and
`hooks/panel.test.tsx`, the lock touch, fence, and record lock cases in
`tests/test_review.py`, and the skill set in `tests/test_packaging.py`.

## 5. Cost

The hot path is the pass, which runs every `watch.interval` seconds while
any session is live. Both budgets are per account: 5,000 GraphQL points and
5,000 counted REST requests an hour.

Today, at the default 60-second interval, for `N` pull requests that both
loops track, where `R` is a pull request's review count:

| Consumer | GraphQL points an hour | REST requests an hour |
| --- | --- | --- |
| Watch loop | `60N`, and `N` more at start | `60N × ceil(R / 30)`, at least `60N` |
| Review loop | `120N` plus 120 to 240 for adoption | None |
| Panel, in each session while shown | About 120, unmeasured | None |
| Both loops at `N = 10` | About 1,920 to 2,040 | At least 600 |
| Both loops at `N = 20` | About 3,720 to 3,840 | At least 1,200 |

After the change, at the same interval, with a full pass every 10 minutes,
`C` changes an hour across the targets, and `B` pushes an hour to a base
that `F` fixed pull requests share:

| Item | GraphQL points an hour | Counted REST requests an hour |
| --- | --- | --- |
| Quiet passes | 0 | 0 |
| Full passes | `12N` | `6N × ceil(R / 100)` for reviews |
| Changes | About `2C`, more when mergeability settles a pass later | About `C`, plus the reviews read on a change |
| Base pushes | `2BF` | `B` |
| Adoption | 0 | One per changed list page |
| A push read by `--from-output` | 0 | One per pushed branch |
| The panel | 0 | 0 |

At `N = 10`, `C = 20`, `B = 5`, and `F = 5`, the daemon spends about 210
points an hour, against about 2,000 today plus 120 for each session showing
the panel. Further sessions on the machine add nothing for the pull requests
they share. A second machine adds about 210.

The daemon sends `2N + 2` conditional requests a pass, about 1,300 an hour
at `N = 10`. A `304` does not count against the primary limit. Whether it
counts toward GitHub's secondary limits is untested. GitHub documents a
secondary limit of 900 points a minute for REST, where a `GET` costs 1, and
the daemon sends about 22 requests a minute at `N = 10`.

The machine's own cost is small. The daemon wakes once a second to read the
modification times of a few files. The mod starts the CLI once a minute per
session, and once per batch of events. A command checks for the daemon with
one lock attempt that does not wait.

These figures count only polling. Each reviewer and fixer also spends
GraphQL, through `project review census` and `publish`, summaries, and
`gh pr view`. That spend grows with the number of heads reviewed and fixed,
and this plan leaves it unchanged. So the daemon's 210 points are a floor on
the hour's total, not the total.

## 6. Rollout

1. **Measure the inferences first.** On a scratch pull request in a
   repository you own, test whether each of these moves the pull request's
   `ETag`: resolving a thread without a reply, a body-only `COMMENT` review,
   and a push to the base. Test whether a conditional request makes GitHub
   recompute `mergeable`, and whether `304` answers count toward the
   secondary limits. Record each result in § 8. Remove the reviews request
   or the base request only where a result shows it redundant, and raise
   `watch.full_every` only if resolving a thread moves the `ETag`.
2. **Ship the change in one release.** The skills call `project watch`, and
   the CLI and both plugins share one version. When the change is too large
   to review as one pull request, open it as a stack that merges as a unit:
   the daemon, `project watch`, and the lock touch and fence in
   `project review`, with their tests, then the skills, their callers, the
   docs, and the packaging tests, then the panel and the mod's delivery.
3. **Let running loops end on their own.** After the upgrade, a session
   keeps its old watcher until the session ends, and the panel lists the old
   watcher's tracked file by its kind meanwhile. `project loop` keeps working
   for one release. A pull request dismissed from an old review loop returns
   under `review-repo` until it is dismissed again.

## 7. Open questions

- **A session's id outside Claude Code.** Deferred to the same question in
  `docs/projects/questions/readme.md` § 7, owned by ninjudd. Until it is
  settled, an agent on another host passes an id it chooses with
  `--session` on each command.
- **Sending an event straight to a running subagent.** Deferred. The mod
  could send a `FINDING` to a live `fix-<n>` with `$.session.send`, as it
  sends an inbox answer, rather than through the main loop. The main loop
  dispatches every event until a follow-up makes that change.
- **Backing off when GraphQL runs out.** Deferred. A rate-limited query
  fails like any other, and the daemon carries the last result forward. A
  daemon that waits for the reset and says once why findings are late needs
  the reset time from GitHub's answer.
- **The inferences in § 2.6.** Deferred to rollout step 1. The design holds
  whichever way each one comes out, and the results only tune
  `watch.full_every` and remove requests.
- **A stack whose layers have different owners.** Ownership is per pull
  request, but one subagent fixes every layer of a stack, so `MOVED` for one
  layer would leave two sessions pushing one stack's branches. Until the
  daemon owns a stack as a unit, `fix-pr` stops its subagent on such a
  `MOVED` and asks the user which session fixes the rest of the stack.
  Deferred, owned by ninjudd.

## 8. Background

What each loop spends today was measured with `rateLimit { cost }` and
`GH_DEBUG=api`, against two open pull requests in this repository, with
`gh` 2.97.0:

- **`watch-threads.sh`** spends 1 GraphQL point per pull request per pass.
  It reads reviews with `gh api repos/<repo>/pulls/<n>/reviews --paginate`
  and no `per_page`, which is `ceil(R / 30)` requests. Every reply inside a
  thread files a review, so `R` grows over a pull request's life. The check
  before the first pass spends one more point per pull request. The default
  interval is 60 seconds, with a floor of 30.
- **`watch-prs.sh`** adopts with two `gh pr list` calls per pass. Each is a
  GraphQL search costing 1 point, plus a `SearchType` introspection costing
  1 point unless `gh`'s local cache answers. It spends 2 points per pull
  request per pass and makes no REST call.
- **The panel** sends one batched GraphQL query every 30 seconds while it is
  shown, `POLL_MS` in `hooks/register.tsx`. This was not measured.

How `gh api` handles a conditional request, as observed:

- A `200` carries a weak `ETag` (`W/"…"`), a `Last-Modified` equal to the
  pull request's `updated_at`, and `Cache-Control: private, max-age=60`.
- A `304` exits `1`, as a `404`, a server error, and a network failure do.
  Only the status line that `-i` prints, `HTTP/2.0 304 Not Modified`, tells
  them apart. With `--jq`, a `304` prints `unexpected end of JSON input`.
- A `304` echoes the `ETag` in its strong form, and either form gets a
  `304`.

What the REST representations hold, as observed: `GET pulls/{n}` has
`mergeable`, `mergeable_state`, `comments`, `review_comments`, `commits`,
`head.sha`, `draft`, `state`, `merged`, and `updated_at`. The open-pulls
list lacks `mergeable`, `mergeable_state`, and the counts, and has `head`,
`draft`, `updated_at`, `user`, `requested_reviewers`, and `requested_teams`.
`requested_teams` is separate, which matches the review loop's rule that
only a request to the login itself adopts a pull request.

What the hosts provide, as observed in Claude Code 2.1.314:

- Every Bash command has `CLAUDE_CODE_SESSION_ID`, equal to the session's
  transcript id and to the folder names `project inbox` uses for the ids the
  mod passes. A subagent's Bash command showed the same id and the same
  `CLAUDE_PID` as the main loop's.
- The mod's declarations type the result of `next(e)` for a Bash call as
  its `stdout` and `stderr`, and offer `$.fs.write`, `$.fs.stat`, and
  `$.process.run`. The mod already submits prompts with `$.prompt.submit`
  and sends to subagents with `$.session.send` to deliver inbox answers.

What a change of review owner meets in `src/projector/review.py` today:

- **The lock comes first.** `setup` calls `take_lock` before it reads
  start comments or `--replace`, and `take_lock` refuses while
  `<sha>.lock` exists and is less than a day old (`STALE_AFTER`), naming the
  loop and host that took it. `--replace` replaces only a start comment, so
  it cannot get a new owner past a lock that a former owner left.
- **The recorded start comment.** `setup` skips the start comment that this
  machine's review state records when it looks for another session's review,
  and deletes it once its own is posted. So on one machine, a new owner
  needs no `--replace`. A start comment that another machine left is not
  recorded here, and `--replace` takes that one over.
- **A live start comment.** `is_live` counts a start comment as a review in
  progress until its model has a verdict on its head or a day has passed.
- **The collision check.** `check_collision` refuses a verdict by the same
  model on the head that the `--loop` record lacks, even under `--rereview`,
  and tells the reviewer to ask the user which review continues.
- **`publish`.** It checks only that `<sha>.lock` exists, and at its end it
  writes the state it read at its start and unlinks the lock.

So a change of owner needs the hand-over in § 2.5, not only `--replace`.

`tests/test_watchers.py` holds the watchers' tests, through a fake `gh` that
refuses any argument it does not know, `-i` and `-H` among them.

## 9. What implementation settled

Building the daemon settled these choices, which the design above left open.

### 9.1 The daemon and the review lock

- **When a line was added.** The daemon records a subscription file's
  modification time the first time it sees each line, in the session's
  `state.json` under `added`, and breaks a tie by the session's id.
- **Precedence among `fix` lines.** The newest explicit line wins. With no
  explicit line, the oldest `auto` line wins, so a later push never takes a
  pull request from the session that pushed first.
- **Owners each pass.** The daemon works out every owner on every pass and
  keeps them in `owners.json` at the state root. A session that gains a role
  starts that role's announcements afresh, so it hears what is outstanding,
  as a resumed session does.
- **`MOVED`.** A live former owner gets `MOVED` whenever the role moves to
  another session, including when the owner removed or dropped its own line.
- **Waiting for a review in flight.** The move waits even when the owner
  removed its line, and the owner hears nothing meanwhile.
- **A lapsed owner's locks.** The daemon clears them on each pull request
  whose review the lapsed session owned, because `review_locks` reads one
  pull request at a time.
- **Lock times.** The daemon counts a lock as changed when it first sees it,
  so after the daemon restarts, a review gets a full `watch.review_stall`
  before it counts as stalled.
- **Adoption.** The pull requests a repository subscription adopted are kept
  in `cache/<owner>/<repo>/adopted.json`, and a closed one is never adopted
  again. Each session's `state.json` lists its adopted pull requests under
  `adopted`, as `{"owner/repo": [12, 14]}` with lowercase keys and sorted
  numbers, leaving out those it excludes. The panel reads that list.
- **Paths.** Cache paths use the lowercase owner and repository. The daemon
  reads review state under GraphQL's `nameWithOwner`, and `review_locks`
  matches a repository without regard to case.
- **`MERGED` and `CLOSED`.** Each session hears either once, recorded in its
  `state.json` under `closed` for 7 days. The daemon then removes the pull
  request's plain lines from every session and keeps its `!` lines.
- **`ETag`s.** The daemon saves a request's `ETag` only after the query it
  led to succeeds, so a change seen while a query fails is queried again. A
  base branch's `ETag` is kept per pull request, and a `404` base is marked
  gone so the pull request is queried once.
- **Event wording.** The details keep the old watchers' wording, with `: `
  before a quoted snippet. A reminder puts `(still open)` after the
  reference, as `FINDING acme/app#12 (still open) …`, so every line starts
  with its kind and its reference. `TRACKED`, `DONE`, and `BRANCH` are gone.
- **Drafts.** Only a draft the operator wrote counts as waiting on a review,
  as the old review loop's `--author` filter did.
- **Older verdict markers.** The daemon recognizes a verdict through
  `verdict_marker`, which needs the marker's model, so a marker from a
  release that wrote none no longer counts toward `RESPONDED`.
- **Commands.** `stop` starts no daemon, `add` does not renew the session,
  and `take` keeps the recorded deliverer, `mod` when none is recorded.
  `remove`, `drop`, and `restore` take `--json`. `status.json` at the state
  root holds what `project watch status` reports.
- **Settings.** `project config set` refuses `watch.interval` below 30 and
  the other `watch` numbers below 1. The daemon reads them only from the
  user's configuration and treats a hand-written interval below 30 as 30.
- **The fence's words.** A refused `publish`, `move`, or `settle` says the
  review "was handed over to another loop", and why. The skills key on that
  phrase.
- **The review mutex.** `publish`'s re-check and `clear_lock` hold
  `projector.inbox.locked` on the pull request's folder,
  `prs/<owner>/<repo>/<pr>/`, not the model's, because `Paths.locks()` globs
  `*.lock`, which also matches the `.lock` that `locked` creates.
- **A review outside any loop.** A review set up with no `--loop` holds a
  lock that names none, and passes the fence as before.
- **The record copy.** `copy_published` returns 0 when the former owner has no
  record, and sorts the merged record by `published_at`, because an
  incremental review reads the last entry as the newest.

### 9.2 The skills

- **Where the review events' rules live.** `review-pr` holds them, and
  `review-repo` cites them, because a session that ran `review-pr` alone
  also hears `NEW HEAD`.
- **When `review-pr` subscribes.** It subscribes after `setup` succeeds, so
  the lock `setup` takes already marks the head in flight and the daemon
  announces no `NEW PR` for the head under review. It also subscribes when
  `setup` refuses because another session's review holds the head, and says
  so.
- **No pull request line under `review-repo`.** A `review-pr` subagent that
  `review-repo` started adds no line of its own, so stopping the
  repository's review leaves no pull request subscribed.
- **Who starts `stream`.** Only the top-level conversation starts
  `project watch stream`. A subagent that reads `events: none` reports it.
- **A host with no session id.** The top-level conversation chooses an id and
  passes it to its subagents with `--session` or `PROJECTOR_SESSION`.
- **Always `--loop`.** Every `review-pr` run passes the session's id as
  `--loop`, so a review a person asks for can be incremental too.
- **An excluded pull request.** When `project watch add` refuses a pull
  request the session excludes, the skill tells the user and runs `restore`
  only when asked.
- **Stopping `review-repo`.** It removes the subscription, stops its
  subagents, and runs `project review finish --force` for each pull request.
- **An explicit `add` after each push.** `fix-pr` runs
  `project watch add` after every push, so running `fix-pr` in another
  session takes the pull request's `fix` role. That is deliberate: the user
  asked that session to fix it.
- **A guard.** `tests/test_packaging.py` fails when any file under `skills/`
  names a removed loop skill, `project loop`, or `--tracked`.

### 9.3 The panel and the mod

- **An earlier watcher's pull requests.** The daemon caches only subscribed
  pull requests, so the panel still fetches the pull requests of an earlier
  watcher's tracked file, and only those, with its 30-second GraphQL query.
  A session with only subscriptions sends no query for its list.
- **How often it reads.** While the panel is shown, it reads the
  subscriptions and the cache on every 5-second tick. The fallback query,
  the drawer's threads, and `panel.background` stay on the 30-second poll.
- **Showing the panel.** It appears when the session gains a subscription it
  lacked, as a new watcher showed it before. The mod keeps the set it has
  seen in its `panelSubscribed` state.
- **Batches.** While a submitted batch still waits for the session, the mod
  takes no more events, and those that arrive meanwhile go as one prompt
  afterwards. The prompt is the event lines alone.
- **Push detection.** The mod splits a command with its existing tokenizer,
  skipping variable assignments, `env`, `command`, `exec`, `nohup`, `time`,
  and git's global options such as `-C <dir>`. It does not look inside
  `bash -c '…'`. It sends the call's standard output and standard error to
  `project watch add --from-output -` on standard input through
  `$.process.run`, and does not wait for it.
- **A loading row.** A pull request with no cache file yet shows no state,
  author, or age, and its drawer names only its roles.
