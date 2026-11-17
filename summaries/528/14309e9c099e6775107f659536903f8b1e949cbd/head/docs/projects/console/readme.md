---
status: in-progress
priority: now
---

# Console

`project console` lists every live session in a terminal, and opens any one
of them as a panel. There you answer or discuss its items and pull requests,
whether the session is Claude Code or Codex, and each answer and each
message reaches the session as a new turn.

## 1. Problem

Projector's panel is how you answer an agent. It sits beside a Claude Code
chat and lists the pull requests the session fixes or reviews. It also lists
the items the agent posted to the inbox: questions you answer, requests you
mark done, and notices you read. You press an option, and the answer reaches
the agent as your prompt. Or you press Discuss and talk the item over in the
chat. The panel is built on Claude Code's plugin API, in
`hooks/register.tsx`.

Codex has nothing like it, so a Codex user has no easy way to answer or to
discuss. They have two ways in, and neither works well:

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
  cannot wait for you while the agent works. § 8 records the evidence.

Answering is the inbox's whole point: an item the agent cannot hear back on
stalls that agent's work. And an answer often needs a conversation first,
which no terminal command offers today.

## 2. Solution

Three changes, built together. First, the watch daemon's Codex adapter
becomes the deliverer of inbox lines for Codex sessions. It claims each
line a Codex session is owed and sends it in the same `codex queue` batch
as that session's watch events. It marks the line delivered when Codex
acknowledges the batch. Second, each inbox item gets a thread, so you can
discuss it without the session's chat: your messages reach the agent as
`DISCUSS` lines, and the agent answers into the thread with
`project inbox reply`. Discussing a pull request starts a new item of kind
`discussion` with the same thread. Third, `project console` is a curses
TUI that you run in a split or a second window. It opens on a list of every
live session, sorted by what needs you. Enter opens that session's panel,
where you answer, discuss, and dismiss for that one session. The console
records your choices and messages in the inbox store. Delivery stays with
each session's own deliverer: Claude Code's panel for a Claude Code
session, and the Codex adapter for a Codex session. So the console works
for both hosts and never sends anything to a session itself.

### 2.1 Answering and discussing from the console

1. You run `project console` in a terminal. It refuses to start when
   standard input or standard output is not a terminal, or when the
   standard library's `curses` module is missing, as on Windows (§ 2.7).
2. The console builds the set of live sessions. That set is the union of
   `inbox.sessions()` and `watch.session_names(root)`, kept where
   `watch.delivery(root, session, now)` is not `none`. It labels each
   session as `project agent list` does: the name, or else the short ID,
   then the host and the repository and branch. The host comes from
   `delivery()`, where `codex` means Codex and `mod` means Claude Code. The
   repository and branch come from `agent.json`, or from `codex.json`'s
   `cwd`.
3. The console shows the home list (§ 2.3). You press Enter on a session,
   and the console shows that session's panel: its pull requests (§ 2.4)
   and its inbox items (§ 2.5).
4. You open an item's drawer and press an option, or type a message with
   Discuss. Or, with no drawer open, you press `7` and type a message to
   start a discussion about the session itself (§ 2.6). The console records
   it with the functions in
   `src/projector/inbox.py`, passing the session you are viewing: `answer`,
   `done`, `decline`, `unclear`, `start`, `dismiss`, `snooze`, `restore`,
   or the new `discuss` (§ 2.6). These are the same functions
   `project inbox` runs. The console never calls `claim`, `delivered`,
   `reopen`, `watch take`, or `watch renew`, because those belong to the
   session's deliverer.
5. When the session is a Codex session, the console runs
   `watch.ensure_daemon`. The daemon can be down while a Codex session is
   idle, and the line would otherwise wait for the next hook.
   `project inbox answer`, `project inbox discuss`, and the other recording
   subcommands do the same for a Codex session.
6. The session's deliverer brings the line in:
   - **A Claude Code session.** The panel's `checkInbox` claims the line on
     its next 5-second tick and submits it as a prompt. That path already
     carries answers recorded from a terminal today.
   - **A Codex session.** The daemon's Codex adapter claims the line and
     sends it, as § 2.2 describes.
7. The item shows `sending` until its line is delivered. An answered item
   leaves the list 30 seconds later, as it does in the panel. A discussed
   item stays open, and the agent's reply appears in its thread.

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
   claim's one-minute lapse. A claim is renewed once it is 20 seconds old,
   in every active session, including one whose batch is being sent, so a
   send that runs to its 60-second timeout cannot outlast the claim.
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
   with `inbox.settle(session, ref, token, delivered=True)`. When the inbox
   stays locked through the settle's 5-second wait, the event waits in
   `codex-unsettled.json`, keeps its claim renewed, and settles on a later
   tick.
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

### 2.3 The home list

Each live session gets one row: its label, host, repository and branch,
and its summary from `agent.json`, then the counts the panel's header
shows (pull requests, review requests, questions, requests, and notices),
then the subject of its newest open item. A session with no open item and
no pull request still gets a row, so you can open its panel and press `7`
to start a discussion about the session (§ 2.5).

Sessions are sorted by what needs you:

1. Sessions with an agent reply you have not read.
2. Then sessions with an open question or request, most of them first.
3. Then sessions with only notices or pull requests.
4. Ties go to the session whose newest item is newest, then to the label.

The row of a session with an unread reply shows `replied` beside the
subject of the item that carries it.

### 2.4 A session's panel: pull requests

The session's panel shows the panel's pull request list for that one
session. It reads the files the panel reads: the session's `subscriptions`
and `state.json`, `daemon.json` for the viewer's login, and the daemon's
cache at `cache/<owner>/<repo>/pulls/<number>.json`. A new module,
`src/projector/rows.py`, turns those files into rows the way
`hooks/github.ts` and `hooks/register.tsx` do:

| Row part | Rule |
| --- | --- |
| Label | `#N` with one repository, `name#N` when names are unique, otherwise `owner/repo#N` |
| State word | The first that holds: `conflict`, `failing`, `changes`, `findings`, `reviewing`, `clean`, then `draft` or `ready` |
| Notes | Every other hold or label, plus `approved`, `passing`, and `cancelled` |
| Checks | Passing, pending, failing, or cancelled, from the cache's check runs |
| Section | Yours first, then review requests grouped by author, each group by repository and then newest number first |

`rows.py` sorts as the panel's `localeCompare` does, by Unicode's root
collation, so `acme/app_b` lists before `acme/app-b`, and both before
`acme/app1`. It lists only the pull requests the session subscribes to.
The panel also lists those an older `watch-prs.sh` or `watch-threads.sh`
watcher tracks, which it learns from the commands it sees the session run.
The console cannot see those commands, so it leaves those pull requests out.

Each file in `tests/fixtures/rows/` is a TypeScript module whose body after
`export default` is JSON: a list of cases, each either the files under the
watch root and the rows they give, or a thread query or response and what
the drawer lists. The panel's test runtime can import a module but cannot
read a file, so `index.ts` imports every case file, and
`tests/test_rows.py` fails when one is missing from it.

The drawer shows the facts line (the session's role, the branch, the size,
the checks, and the labels) and the unresolved threads by file. The
daemon's cache lacks the thread fields the drawer shows, so `rows.py` also
carries the panel's thread query. It runs as a `gh api graphql` call for
that one pull request when the drawer opens, and every 30 seconds while the
drawer stays open. It is skipped when the pull request has no unresolved
thread. The drawer's actions are these:

| Key | Action |
| --- | --- |
| `7` | Discuss: opens a text field, and starts a `discussion` item about this pull request in this session (§ 2.6) |
| `8` | Dismiss, after a confirmation. The console runs `project watch drop <owner>/<repo>#<n> --role <role> --session <s>` for each role this session holds |
| `o` | Opens the pull request in a browser with `webbrowser.open` |
| `p` | Opens its projector.bot summary, when the description carries one |

### 2.5 A session's panel: inbox items

Each item line shows the subject, the state words the panel shows
(`sending`, `unclear`, `starting`, `ready`, `blocked`, and the new
`replied`), the pull request, and the age. The item drawer shows the detail
as wrapped text, then the item's thread, then the panel's controls:

| Kind | Controls |
| --- | --- |
| Question | `1`… for the options, the first marked `(recommended)`. The last option, `type an answer`, opens a one-line text field |
| Request | `1: Start` or `Retry` when the item has a start action, then the choices, or `Done` |
| Discussion | No options. It stays open until you dismiss it or the agent withdraws it |
| Any open item | `6: I don't understand`, `7: Discuss`, `8: Dismiss` or `Restore`, and `9: Snooze`, whose options are 15 minutes, 1 hour, 4 hours, and 1 day |

Opening a drawer marks the agent's replies in its thread read.

With no drawer open, `7` opens a text field and starts a `discussion` item
about the session itself (§ 2.6), whichever row the cursor is on. It works
in a panel with no rows, such as an idle session's or a new one's. To
discuss a particular item or pull request, open its drawer and press `7`
there.

### 2.6 Threads

An item's thread holds the messages you and the agent exchange about it.
Each entry is one paragraph of up to 4 KiB, which keeps a `DISCUSS` line
well inside the adapter's batch limit.

| Item field | Value |
| --- | --- |
| `thread` | A list of entries, oldest first. Absent until the first message |
| `thread[].from` | `person` or `agent` |
| `thread[].text` | The message |
| `thread[].at` | When it was recorded, in milliseconds |
| `thread[].deliveredAt` | Set on a `person` entry when its line is delivered |
| `thread[].readAt` | Set on an `agent` entry when you open the item's drawer, in the console or in the panel |

A `discussion` item is an item of a new kind that you start, about a pull
request or about the session itself. It has a thread whose first entry is
your message, and it takes the next number in the session's count, like any
other item. Its subject says what it is about:

| About | `pr` | `subject` |
| --- | --- | --- |
| A pull request | `<owner>/<repo>#<n>` | `About <owner>/<repo>#<n>` |
| The session | Absent | `About this session` |

The commands:

| Command | Who runs it | What it does |
| --- | --- | --- |
| `project inbox discuss <number> --text <words> --session <s>` | You, through the console or a terminal | Adds a `person` entry to an open item's thread |
| `project inbox discuss --pr <owner>/<repo>#<n> --text <words> --session <s>` | You | Starts a `discussion` item about that pull request, and prints its number |
| `project inbox discuss --text <words> --session <s>` | You | Starts a `discussion` item about the session itself, and prints its number |
| `project inbox reply <number> --text <words> --session <s>` | The agent | Adds an `agent` entry to the thread. It needs no delivery, because you read it in the console or the panel |

Each undelivered `person` entry owes a line, after the lines the item
already owes in this order: `answer`, `restored`, `started`, and `unclear`.
An item has one claim at a time, so its entries are delivered one at a
time, oldest first. The line is
`DISCUSS 3 Decline the finding about the retry budget? → <words>`, or
`DISCUSS 5 About this session → <words>` for a discussion about the
session. `discuss` refuses a number together with `--pr`.

The agent treats a `DISCUSS` line as a reply to the item. It answers with
`project inbox reply`, or with the panel's new `reply` tool in Claude Code.
Then it withdraws the item if the discussion settles it, revises it if the
discussion changed the choice, or leaves it open. It answers in its chat as
well, because you may be reading either one. `src/projector/templates/agents-block.md`
and `skills/work/SKILL.md` say so beside their instructions for `ANSWER`
lines. In the panel, `7: Discuss` still fills Claude Code's prompt, and
the drawer shows the item's thread.

Building the threads settled these details:

- **The claim names its entry.** A `DISCUSS` claim records `line: discuss`
  and the index of the entry it carries in `entry`, so `message` and
  `settle` act on that entry even when another arrives meanwhile.
  `settle(delivered=True)` sets the entry's `deliveredAt`.
  `settle(delivered=False)` removes the entry, as a failed `UNCLEAR` is
  taken back, and drops `thread` once it is empty, so you say it again. The
  panel's toast for it reads "your message about … did not reach its agent;
  send it again".
- **Your words are owed whatever becomes of the item.** An entry stays owed
  after you answer, dismiss, or withdraw the item, so a message sent just
  before a dismissal still arrives. Its claim survives an answer, an
  outcome, a dismissal, and a restore, as a claimed `STARTED` or `UNCLEAR`
  does, and the answer's line follows once it settles. Pruning keeps a
  delivered item that still owes a line.
- **A discussion is notice-like.** `answer` refuses it as a kind that takes
  no answer, `revise` refuses it because its subject names what it is
  about, and `done`, `decline`, and `start` refuse it as they refuse any
  kind but a request. Its dismissal is delivered at once and sends nothing,
  and a restore sends no `RESTORED`. `snooze`, `restore`, `withdraw`, and
  `unclear` work on it.
- **Reading is a command.** `project inbox read <number> --session <s>`
  sets `readAt` on the item's agent entries. `discuss` and `read` record
  your words and what you saw, so both join `ASK` and `ASKED` and stay out
  of `CODEX_ALLOW`. `reply` joins `CODEX_ALLOW`. `reply` refuses a resolved
  item and tells the agent to answer in the conversation instead, since you
  no longer see its thread.
- **A message is one line.** "One paragraph" means no line break, so a
  `DISCUSS` line stays one line for `watch`, and the 4 KiB limit counts
  UTF-8 bytes.
- **What shows it.** `list` shows an open item with an unread reply as
  `replied`, and `show` prints the thread as `you:` and `agent:` lines. The
  panel draws a discussion in the theme's `text` color and `replied` in
  `suggestion`. A line reads `sending` while it owes a message. The panel
  runs `read` when a drawer opens on an item with an unread reply, on each
  inbox check while that drawer stays open and the panel is shown, and when
  `/projector` shows the panel again with that drawer open. A hidden panel
  runs no `read`, so a reply that arrives while it is hidden keeps
  `replied`. The `reply` tool registers after `restore`. The template in `agents-block.md` moves to version 30.

### 2.7 Running the console

```console
$ tmux split-window -h project console
$ project console
```

| Key | Effect |
| --- | --- |
| Up and down, or `k` and `j` | Move between rows |
| Enter | On the home list, opens the session's panel. In a panel, opens or closes the drawer of the row under the cursor |
| Esc or `0` | Closes the drawer, or, with no drawer open, returns to the home list |
| `7` in a panel with no drawer open | Starts a discussion about the session (§ 2.5) |
| Left | Returns to the home list from a session's panel, closing any open drawer. In a text field, it moves the cursor instead |
| Enter in a text field | Sends the message. Esc cancels it |
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

### 2.8 Changes by file

| File | Change |
| --- | --- |
| `src/projector/codex_watch.py` | Claims, renews, batches, and settles inbox lines (§ 2.2). Adds the inbox key to `eligible`, sends an oversized event alone, counts owed lines in `has_work`, keeps inbox events out of `drain`, and rewords `message` and the hook context |
| `src/projector/inbox.py` | Adds `thread`, the `discussion` kind, `discuss`, `reply`, and marking replies read. `owed_line`, `message`, and `settle` handle `DISCUSS` lines |
| `src/projector/rows.py` | New. Turns subscriptions and the cache into rows, and queries and parses threads |
| `src/projector/console.py` | New. Holds the session set, the model of what is on screen, key handling, and the curses drawing, kept separate so tests drive the model without a terminal |
| `src/projector/cli.py` | Adds `project console`, `project inbox discuss`, and `project inbox reply`. The inbox recording subcommands run `ensure_daemon` for a Codex session |
| `src/projector/permissions.py` | Adds `Bash(project console)`, `Bash(project console *)`, and `Bash(project inbox discuss *)` to `ASK` and `ASKED`. Adds `reply` to the inbox subcommands in `CODEX_ALLOW`. Leaves `console` and `discuss` to Codex's approval, and updates the docstring and the `CODEX_RULES` header |
| `hooks/inbox.ts`, `hooks/register.tsx` | `needsDelivery` counts undelivered `person` entries. The drawer shows the thread and marks replies read. The item list draws `discussion` items and `replied`. Registers the `reply` tool |
| `src/projector/templates/agents-block.md`, `skills/work/SKILL.md` | Describe `DISCUSS` lines and `reply`. `skills/work/SKILL.md` also tells a session not to run `project inbox watch` when its session-start context says Projector delivers its events |
| `tests/fixtures/rows/` | New. Each case is a set of input files plus the rows they must produce |
| `hooks/rows.test.ts` | New. Runs the panel's row model over `tests/fixtures/rows/` |
| `tests/test_console.py`, `tests/test_rows.py` | New |
| `tests/test_codex_watch.py`, `tests/test_inbox.py`, `tests/test_permissions.py`, `hooks/inbox.test.ts`, `hooks/panel.test.tsx` | New cases (§ 4) |
| `docs/cli.md` | A new "Use the console" section. "Use the inbox" describes threads, `discuss`, `reply`, the Codex adapter's delivery, and the permission rules |
| `docs/plugins.md` | Describes the thread in the drawer and the `reply` tool, and says that `project console` covers Codex and terminals, where it said Codex has no equivalent |
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
- **The thread lives on the item.** In Claude Code, Discuss works because
  your words and the agent's reply both happen in the chat. In the console,
  the agent's reply would land in a chat you are not reading. So the agent
  answers into the item, where you are looking. Agent messages to the
  session, through `project agent send`, were rejected: they carry none of
  the person's authority, and your words must.
- **`discuss` is a recording command, and `reply` is not.** `discuss`
  speaks for you, so it gets the permission rules `answer` has, and an
  agent cannot put words in your mouth. `reply` speaks for the agent, so
  agents run it freely.
- **The home list is per session.** You chose a list of sessions over one
  combined view. Each session's panel then scopes every action to that
  session: Dismiss drops a pull request only from the session you are
  viewing, a pull request two sessions hold shows each session's own role,
  and Discuss always has exactly one session to talk to. Items of a
  session that is no longer live are left out, because nothing could
  deliver to it.
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
| Discuss item 3 twice, then let the Codex adapter and, separately, the panel deliver | Two `DISCUSS 3` lines, in the order you sent them, each delivered once, and item 3 still open | Entries delivered out of order or twice, or a discussion that closes the item |
| Have the agent run `project inbox reply 3 --text …` | The home list sorts that session first and shows `replied`. Opening the drawer shows the reply and clears `replied` | A reply nobody sees, or one that stays unread after you read it |
| Open the panel of a session with no item and no pull request, press `7`, and send `hello` | A `discussion` item with no `pr` and the subject `About this session`, and the session's deliverer sends `DISCUSS <n> About this session → hello` | An empty session that you can open but cannot talk to |
| Run `project inbox discuss --text hello --session <s>`, then the same with `3 --pr acme/app#12` | The first prints the new item's number. The second exits with an error and records nothing | A session discussion missing from the CLI, or an ambiguous call that picks one target |
| Discuss a pull request that two sessions hold, from the second session's panel | A `discussion` item about it in the second session only, with your words as the first entry | A discussion started in the wrong session |
| Start the console with one Claude Code session renewed by `mod`, one Codex session, and one session whose `session.json` is 10 minutes old | Home rows for the first two, each labelled with its host. None for the third | A session set that includes dead sessions, or one that misses a host |
| On the home list, give one session an unread reply and another two open questions | The session with the reply comes first | A sort that ignores replies |
| Two sessions each own an item 3. In the second session's panel, press option `a` | Only that session's item 3 is answered | A recording call made with the wrong session |
| Answer from the console for a Claude Code session | The panel delivers it. `hooks/panel.test.tsx` already covers an answer recorded outside the panel | Covered by the existing test |
| Answer or discuss from the console | The item has no `claim` and no `deliveredAt` | A console that delivers, racing the session's deliverer |
| Run each fixture in `tests/fixtures/rows/` through `rows.py` and through the panel's model | Both produce the fixture's expected rows | A state, note, check, or section rule changed in one copy only |
| Open a drawer for a pull request with two unresolved threads, and wait 65 seconds | Three thread queries. With no unresolved thread, none | A thread query on every tick, or a drawer that never refreshes |
| Dismiss a pull request in one session's panel when another session also holds it | `watch drop` runs for the viewed session's roles only, and the other session keeps it | A dismiss that reaches beyond the viewed session |
| Run `project console </dev/null` | Exit 69 with the terminal message | A console that starts without a terminal |
| Decide `project console`, `project inbox discuss`, and `project inbox reply` in each host | `console` and `discuss` ask in Claude Code and are left to Codex's approval. `reply` runs freely in both | An agent allowed to drive the console or speak for you, or one blocked from replying |

## 5. Cost

- **The console's local reads.** Each second, the console lists one
  directory per live session and compares signatures. It reads item files
  only from a session whose signature changed. Every 5 seconds, it reads
  each subscribed pull request's cache file for the home list's counts.
  Measure one console with 10 live sessions and 30 pull requests at under
  2% of one CPU core.
- **GitHub quota.** The rows cost no GraphQL, because the daemon's cache
  supplies them. The thread query runs only while a drawer is open. It costs
  about one point of the account's hourly GraphQL quota per query, the same
  as the panel's query. Two consoles and a panel that open the same drawer
  each pay for their own query.
- **The daemon tick.** Each tick now scans each active Codex session's inbox
  directory for owed lines. That is one directory listing per Codex
  session, beside the folder reads the tick already does.

## 6. Rollout

The four pull requests merge in this order. Each one works on its own.

1. Codex delivery (§ 2.2), with the `ensure_daemon` call, the
   `skills/work` change, and its tests. After it merges,
   `project inbox answer` in a terminal reaches a Codex session.
2. Threads (§ 2.6): `discuss`, `reply`, the `discussion` kind, the
   panel's thread and `reply` tool, and the agent instructions. After it
   merges, `project inbox discuss` in a terminal works with both hosts.
3. `rows.py`, the shared fixtures, and `hooks/rows.test.ts`.
4. `project console`, its permission rules, and the documentation.

Open them as a stack with `gh stack`, so each layer stays on the current
head of the one below.

## 7. Open questions

- **A combined view across sessions.** Deferred. The home list's counts
  and newest subjects cover triage. A key that shows every session's items
  in one list can follow if the home list proves too slow.
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
