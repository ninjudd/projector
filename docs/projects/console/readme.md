---
status: ready
priority: now
---

# Console

`project console` gives you one terminal view of every live session's pull
requests and inbox. It lets you answer any item there, whether a Claude Code
session or a Codex session posted it, and each answer reaches its session
as a new turn.

## 1. Problem

Projector's panel is how you answer an agent. It sits beside a Claude Code
chat and lists the pull requests the session fixes or reviews. It also lists
the items the agent posted to the inbox: questions you answer, requests you
mark done, and notices you read. You press an option, and the answer reaches
the agent as your prompt. The panel is built on Claude Code's plugin API, in
`hooks/register.tsx`.

Codex has nothing like it, and so a Codex user has no easy way to answer.
They have two ways in, and neither works well:

- **`project inbox answer` in another terminal records the answer, but
  nothing brings it into the session.** A Codex session hears an answer only
  when its agent keeps `project inbox watch` running or polls it with
  `--once` between steps, as `skills/work/SKILL.md` tells it to. An idle
  session never polls. The watch daemon's Codex adapter in
  `src/projector/codex_watch.py` already wakes an idle Codex session for pull
  request events with `codex queue`, but it carries no inbox lines.
- **Codex gives a plugin nowhere to draw a panel.** Codex's TUI renders only
  built-in status line items. Statusline providers backed by MCP exist on
  Codex's `main` branch but are not wired in yet, and they show text only.
  An MCP elicitation form can be raised only during an active turn, so it
  cannot wait for you while the agent works. § 7 records the evidence.

Answering is the inbox's whole point: an item the agent cannot hear back on
stalls that agent's work.

## 2. Solution

Two changes, built together. First, the watch daemon's Codex adapter
becomes the deliverer of inbox lines for Codex sessions. It claims each
line a Codex session is owed and sends it in the same `codex queue` batch
as that session's watch events. It marks the line delivered when Codex
acknowledges the batch. Second, `project console` is a curses TUI that you
run in a split or a second window. It shows every live session's pull
requests and inbox items, and records your answers in the inbox store.
Delivery stays with each session's own deliverer: Claude Code's panel for a
Claude Code session, and the Codex adapter for a Codex session. So the
console works for both hosts and never sends anything to a session itself.

### 2.1 Answering from the console

1. You run `project console` in a terminal. It refuses to start when
   standard input or standard output is not a terminal, or when the
   standard library's `curses` module is missing, as on Windows (§ 2.5).
2. The console builds the set of live sessions. That set is the union of
   `inbox.sessions()` and `watch.session_names(root)`, kept where
   `watch.delivery(root, session, now)` is not `none`. It labels each
   session as `project agent list` does: the name, or else the short ID,
   then the host and the repository and branch. The host comes from
   `delivery()`, where `codex` means Codex and `mod` means Claude Code. The
   repository and branch come from `agent.json`, or from `codex.json`'s
   `cwd`.
3. The console draws two sections, laid out as the panel lays them out. The
   pull request list covers every pull request any live session is
   subscribed to, so a pull request two sessions hold appears once. Below
   it, the inbox items of every live session appear newest first, each with
   its session's label.
4. You open an item's drawer and press an option. The console records the
   choice with the functions in `src/projector/inbox.py`, passing the item's
   own session: `answer`, `done`, `decline`, `unclear`, `start`, `dismiss`,
   `snooze`, or `restore`. These are the same functions `project inbox`
   runs. The console never calls `claim`, `delivered`, `reopen`,
   `watch take`, or `watch renew`, because those belong to the session's
   deliverer.
5. When the item's session is a Codex session, the console runs
   `watch.ensure_daemon`. The daemon can be down while a Codex session is
   idle, and the line would otherwise wait for the next hook.
   `project inbox answer` and the other recording subcommands do the same
   for a Codex session.
6. The session's deliverer brings the line in:
   - **A Claude Code session.** The panel's `checkInbox` claims the line on
     its next 5-second tick and submits it as a prompt. That path already
     carries answers recorded from a terminal today.
   - **A Codex session.** The daemon's Codex adapter claims the line and
     sends it, as § 2.2 describes.
7. The item shows `sending` until its line is delivered, then leaves the
   list 30 seconds later, as it does in the panel.

### 2.2 Delivering inbox lines to Codex

On each daemon tick, `Dispatcher.tick` in `codex_watch.py` does the
following for each active Codex session before it calls `prepare`:

1. It claims every line `inbox.undelivered(session)` returns and appends
   each line to the session's queue as an event. The event's text is
   `inbox.message(claimed)` and its key is
   `["inbox", ref, line, token]`. The claim is the lock that keeps a second
   deliverer from sending the same line.
2. It renews the claim of every inbox event that is still queued or in a
   pending batch, so a batch can wait in Codex's queue longer than the
   claim's one-minute lapse.
3. `prepare` builds the batch as it does today. Inbox events and watch
   events share the one outstanding batch per session, the 8 KiB limit, and
   the `PROJECTOR WATCH <uuid>` marker. An inbox event larger than the limit
   is sent alone in a batch of its own, so it can never wedge the queue.
4. `eligible` accepts an inbox event while the item's claim still carries
   that event's token. A restore, an answer, or a revision replaces or
   drops the claim. When that happens, the stale line leaves the queue, and
   the next tick claims the line the item now owes.
5. When Codex acknowledges the batch, through the `UserPromptSubmit` hook
   or the transcript scan in `confirm`, `consume` settles each inbox event
   with `inbox.settle(session, ref, token, delivered=True)`.
6. A failed `codex queue` call keeps the claims and retries with the
   adapter's existing backoff. It never settles with `delivered=False`,
   because that deletes the person's answer.
7. When the session disconnects, `drain` leaves inbox events out of the
   plain `events` file and stops renewing their claims. The claims lapse
   within a minute, and the line goes to whichever deliverer the session
   has next.

`has_work` counts owed inbox lines, so a Codex session with an answer
waiting keeps the daemon alive. The batch header changes from "Recheck
current subscriptions and PR state before acting." to "Recheck current
subscriptions, pull request state, and inbox items before acting." The
hook's context changes from "Process only these still-subscribed events:"
to "Process only these still-current events:". The `SessionStart` hook's
context also tells the agent to pass the same `--session` to
`project inbox` commands as to `project watch` commands, so its items land
under the ID the adapter delivers to. It also says that Projector delivers
the session's answers, so the agent runs no `project inbox watch`.

### 2.3 The console's pull request list

The console shows the panel's pull request list. It reads the same files:
each session's `subscriptions` and `state.json`, `daemon.json` for the
viewer's login, and the daemon's cache at
`cache/<owner>/<repo>/pulls/<number>.json`. A new module,
`src/projector/rows.py`, turns those files into rows the way
`hooks/github.ts` and `hooks/register.tsx` do:

| Row part | Rule |
| --- | --- |
| Label | `#N` with one repository, `name#N` when names are unique, otherwise `owner/repo#N` |
| State word | The first that holds: `conflict`, `failing`, `changes`, `findings`, `reviewing`, `clean`, then `draft` or `ready` |
| Notes | Every other hold or label, plus `approved`, `passing`, and `cancelled` |
| Checks | Passing, pending, failing, or cancelled, from the cache's check runs |
| Section | Yours first, then review requests grouped by author, each group by repository and then newest number first |

The drawer shows the facts line (each holding session with its role, the
branch, the size, the checks, and the labels) and the unresolved threads by
file. The daemon's cache lacks the thread fields the drawer shows, so
`rows.py` also carries the panel's thread query. It runs as a `gh api
graphql` call for that one pull request when the drawer opens, and every 30
seconds while the drawer stays open. It is skipped when the pull request
has no unresolved thread. The drawer's actions are these:

| Key | Action |
| --- | --- |
| `o` | Opens the pull request in a browser with `webbrowser.open` |
| `p` | Opens its projector.bot summary, when the description carries one |
| `8` | Dismisses it, after a confirmation that names each session holding it. The console runs `project watch drop <owner>/<repo>#<n> --role <role> --session <s>` for each holding session and role |

### 2.4 The console's inbox items

Each item line shows the subject, the session's label, the state words the
panel shows (`sending`, `unclear`, `starting`, `ready`, `blocked`), the pull
request, and the age. The item drawer shows the detail as wrapped text, with
the panel's controls:

| Kind | Controls |
| --- | --- |
| Question | `1`… for the options, the first marked `(recommended)`. The last option, `type an answer`, opens a one-line text field |
| Request | `1: Start` or `Retry` when the item has a start action, then the choices, or `Done` |
| Any open item | `6: I don't understand`, `8: Dismiss` or `Restore`, and `9: Snooze`, whose options are 15 minutes, 1 hour, 4 hours, and 1 day |

The panel's `7: Discuss` puts a reference in Claude Code's prompt. The
console has no prompt to fill, so it has no `Discuss`. You discuss an item
in the session's own chat.

### 2.5 Running the console

```console
$ tmux split-window -h project console
$ project console
```

| Key | Effect |
| --- | --- |
| Up and down, or `k` and `j` | Move between rows |
| Enter | Opens or closes the drawer of the row under the cursor |
| Esc or `0` | Closes the drawer |
| `q` | Quits |

The console reads the inbox store each second. It compares each live
session's directory signature (names, sizes, and modification times) and
reloads only the sessions whose signature changed. It reloads the pull
request rows from the cache every 5 seconds. It draws with the terminal's
own colors through `curses.use_default_colors`, and it honors `NO_COLOR`.
`panel.background` and Claude Code's themes stay the panel's settings.

Without a terminal, `project console` exits with code 69 and the message
`project console needs a terminal; run it in its own window or split`.
Without `curses`, it also exits with code 69 and points you to
`project inbox list` and `project watch list`.

### 2.6 Changes by file

| File | Change |
| --- | --- |
| `src/projector/codex_watch.py` | Claims, renews, batches, and settles inbox lines (§ 2.2). Adds the inbox key to `eligible`, sends an oversized event alone, counts owed lines in `has_work`, keeps inbox events out of `drain`, and rewords `message` and the hook context |
| `src/projector/inbox.py` | No new state. The console and the adapter reuse `undelivered`, `claim`, `settle`, `message`, and the recording functions |
| `src/projector/rows.py` | New. Turns subscriptions and the cache into rows, and queries and parses threads |
| `src/projector/console.py` | New. Holds the session set, the model of what is on screen, key handling, and the curses drawing, kept separate so tests drive the model without a terminal |
| `src/projector/cli.py` | Adds `project console`. The inbox recording subcommands run `ensure_daemon` for a Codex session |
| `src/projector/permissions.py` | Adds `Bash(project console)` and `Bash(project console *)` to `ASK` and `ASKED`, and updates the docstring and the `CODEX_RULES` header to leave `console` to Codex's approval |
| `skills/work/SKILL.md` | Tells a session not to run `project inbox watch` when its session-start context says Projector delivers its events, because the daemon then delivers its answers too. The Codex `SessionStart` hook registers every Codex session, so that context reaches each one, before it subscribes to anything |
| `tests/fixtures/rows/` | New. Each case is a set of input files plus the rows they must produce |
| `hooks/rows.test.ts` | New. Runs the panel's row model over `tests/fixtures/rows/` |
| `tests/test_console.py`, `tests/test_rows.py` | New |
| `tests/test_codex_watch.py`, `tests/test_permissions.py` | New cases (§ 4) |
| `docs/cli.md` | A new "Use the console" section. "Use the inbox" says the Codex adapter delivers answers, and describes the permission rule for `console` |
| `docs/plugins.md` | Says that `project console` covers Codex and terminals, where it said Codex has no equivalent |
| `web/components/sections/panel.tsx` | Names `project console` for Codex and terminals |

## 3. Why this design

- **Delivery stays with each session's deliverer.** The console could send
  answers itself with `codex queue` or a prompt, but then two processes
  would deliver to the same session. The inbox's claim protocol exists so
  that exactly one deliverer sends each line. The Claude Code panel already
  delivers answers recorded outside it. The Codex adapter already wakes an
  idle Codex session and confirms receipt. Both stay the only senders.
- **Inbox lines share the watch batch.** A second `codex queue` call per
  session would mean two prompts outstanding for one thread, and a second
  marker and receipt scheme. One batch keeps the adapter's single
  outstanding batch, its receipts, and its guard against a repeated
  marker.
- **A failed send keeps the answer.** `settle(delivered=False)` reopens the
  question, which is right when a person must answer again. A queue
  failure is the adapter's problem, not the person's, so the adapter keeps
  the claim and retries.
- **The console shows every live session.** One window covers every agent
  you run, so you never need to know which session posted an item. The
  inbox already stores items per session, and every recording function
  takes the session, so this costs no new state. Items of a session that
  is no longer live are left out, because nothing could deliver them.
- **The row model is ported to Python, with shared fixtures.** The panel's
  row rules live only in TypeScript. Two alternatives were rejected:
  - **Have the daemon write rows that both views read.** The panel also
    shows pull requests held by older `watch-threads.sh` and `watch-prs.sh`
    watchers. The daemon never caches those, so the panel would keep a
    TypeScript copy anyway.
  - **Have the console call into the panel's code.** That needs Node and
    Claude Code's runtime in a plain terminal.

  The fixtures in `tests/fixtures/rows/` run through both copies, so a rule
  that changes in one copy fails the other's tests.
- **Curses from the standard library.** The CLI has no runtime
  dependencies and supports Python 3.11. `curses` gives a full-screen TUI
  on macOS and Linux with nothing to install. A dependency such as Textual
  would add an install and release cost for one command, and Windows
  support would still need `windows-curses`.
- **Agents are kept out by permission rules, not detection.** An agent's
  shell in Claude Code has no terminal, so the console refuses to start
  there. Codex can give a command a terminal that the agent types into, so
  `console` is left out of `CODEX_ALLOW` and needs your approval. `ASK`
  covers Claude Code in case a future mode gives commands a terminal.
- **The name is `console`.** "Panel" names the Claude Code view of one
  session. A separate name for the view of every session keeps the two
  apart in the docs.

## 4. Acceptance criteria

| Do | Expect | Catches |
| --- | --- | --- |
| Record `answer 3.a` for an idle Codex session, with a fake `codex` executable | One `codex queue --thread <id> --resume` call whose message holds `ANSWER 3.a …`. After the `UserPromptSubmit` acknowledgment, item 3 has `deliveredAt` | An adapter that never claims inbox lines |
| Keep that batch queued for 3 minutes while `project inbox watch --session <id>` runs | `watch` prints nothing for item 3, and `codex queue` runs once | A claim that lapses while its batch waits, so a second deliverer sends the line again |
| Restore a dismissal whose `DISMISSED` line is queued but not yet sent | The next batch holds `RESTORED` and not `DISMISSED` | A stale line sent after its claim was dropped |
| Make `codex queue` exit 1 | Item 3 keeps its answer, and the adapter retries after its backoff | A failure that reopens the person's question |
| Disconnect the session with an inbox event queued | `events` holds no inbox line, and `inbox.undelivered` returns the item after 60 seconds | Inbox lines drained into a file whose reader never settles them |
| Record an answer whose text is 9 KiB | It goes out alone, and the watch event queued behind it goes out in the next batch | An oversized line that wedges the queue |
| Record an answer for a Codex session while the daemon is stopped | The daemon is running when the command exits | An answer that waits for the session's next hook |
| Start the console with one Claude Code session renewed by `mod`, one Codex session, and one session whose `session.json` is 10 minutes old | Items from the first two, each labelled with its host. None from the third | A session set that includes dead sessions, or one that misses a host |
| Two sessions each own an item 3. Press option `a` on the second session's item | Only that session's item 3 is answered | A recording call made without the item's session |
| Answer from the console for a Claude Code session | The panel delivers it. `hooks/panel.test.tsx` already covers an answer recorded outside the panel | Covered by the existing test |
| Answer from the console | The item has no `claim` and no `deliveredAt` | A console that delivers, racing the session's deliverer |
| Run each fixture in `tests/fixtures/rows/` through `rows.py` and through the panel's model | Both produce the fixture's expected rows | A state, note, check, or section rule changed in one copy only |
| Open a drawer for a pull request with two unresolved threads, and wait 65 seconds | Three thread queries. With no unresolved thread, none | A thread query on every tick, or a drawer that never refreshes |
| Dismiss a pull request held by two sessions with different roles | One `watch drop` for each session and role, and the row leaves the list | A dismiss that drops it from one session only |
| Run `project console </dev/null` | Exit 69 with the terminal message | A console that starts without a terminal |
| Decide `project console` in each host | `ask` in Claude Code. Left to Codex's approval | An agent allowed to drive the console |

## 5. Cost

- **The console's local reads.** Each second, the console lists one
  directory per live session and compares signatures. It reads item files
  only from a session whose signature changed. Every 5 seconds, it reads
  each subscribed pull request's cache file. Measure one console with 10
  live sessions and 30 pull requests at under 2% of one CPU core.
- **GitHub quota.** The rows cost no GraphQL, because the daemon's cache
  supplies them. The thread query runs only while a drawer is open. It costs
  about one point of the account's hourly GraphQL quota per query, the same
  as the panel's query. Two consoles and a panel that open the same drawer
  each pay for their own query.
- **The daemon tick.** Each tick now scans each active Codex session's inbox
  directory for owed lines. That is one directory listing per Codex
  session, beside the folder reads the tick already does.

## 6. Rollout

The three pull requests can merge in this order. Each one works on its own.

1. Codex delivery (§ 2.2), with the `ensure_daemon` call, the `skills/work`
   change, and its tests. After it merges, `project inbox answer` in a
   terminal reaches a Codex session.
2. `rows.py`, the shared fixtures, and `hooks/rows.test.ts`.
3. `project console`, the permission rules, and the documentation.

Open them as a stack with `gh stack`, so each layer stays on the current
head of the one below.

## 7. Open questions

- **Discussing an item from the console.** Deferred. The console has no
  prompt to fill. A reply typed in the console could be delivered as a line
  of its own, but that needs a new kind of line in the inbox. Owner:
  whoever builds the console, if users ask for it.
- **Dismissing from one session only.** Deferred. The first version drops
  a pull request from every session that holds it, after a confirmation
  that names them.
- **Showing items from sessions that are no longer live.** Deferred. Such
  an item cannot be delivered, so it would need its own state in the
  console.
- **Windows.** Deferred. The console exits there with a pointer to
  `project inbox list`.
- **Codex's own surfaces.** Deferred until Codex ships them. A statusline
  provider could show the count of open items in Codex's footer, and an
  elicitation form could answer an item while the agent runs one of a
  Projector MCP server's tools.

## 8. Background

### 8.1 What Codex offers

Checked against `codex-cli 0.169.0` and `openai/codex` at `d8d840d` on
2026-11-17:

- **Built-in status line.** `tui.status_line` accepts only Codex's
  built-in items. Codex issue #17827 asks for a script hook, and the
  third-party footers that exist patch the Codex binary.
- **Statusline providers.** An MCP tool advertises a `ui://` resource as
  an `openai/ui` entrypoint of type `statusline`. The TUI reads that
  resource for the thread on screen. The provider code landed on
  2026-11-13, behind the `tui_extensions_statusline` flag, which is still
  under development. Nothing in the TUI calls it yet, and Codex rejects a
  provider registered through project configuration.
- **Sidebar documents.** `tui_workspace` adds a sidebar with a read-only
  Markdown preview. A preview opens only when you follow a file link in
  the transcript, and it does not reload when its file changes.
- **MCP elicitation.** The TUI renders single-select forms, but
  `request_mcp_server_elicitation` requires an active turn.
- **MCP Apps.** HTML widgets attach to a tool call's result in the
  conversation, behind `enable_mcp_apps`, which is still under
  development.

### 8.2 What the panel and the adapter do today

- **The panel answers through the prompt.** Pressing an option fills the
  prompt with the quoted item and the option. The `prompt.submit` hook
  records the answer and marks it delivered, because the prompt carried it.
  For answers recorded elsewhere, the panel claims each line its session
  owes, submits it as a prompt, and renews the claim each tick
  (`hooks/register.tsx`, `checkInbox` and `deliver`).
- **The adapter drops keys it does not know.** `eligible` drops any queued
  event whose key is not a pull request event, a peer message, or a
  terminal notice. That is why inbox events need the key shape and the
  eligibility rule in § 2.2.
- **Inbox directories outlive their sessions.** Nothing prunes a session's
  inbox directory. Only the cap of 100 delivered items applies, so the
  console has to filter by liveness.
- **Sessions that only run `project inbox watch` renew nothing**, so no
  liveness check can see them. After this project, a Codex session is
  delivered by the adapter, and a Claude Code session by the panel, so
  both are visible.
