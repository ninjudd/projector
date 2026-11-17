---
status: draft
priority: later
---

# Projector's panel in Paseo

A [Paseo](https://paseo.sh) plugin shows each agent's Projector inbox items
and pull requests above its composer and in a panel beside it, records your
answers through `project inbox`, and delivers each answer and pull request
event to the agent as a new turn, on Paseo's desktop, web, and phone apps.

## 1. Problem

Paseo runs coding agents, Claude Code and Codex among them, from a daemon
on your machine, and its desktop, web, and phone apps connect to that
daemon. It drives Claude Code through the Claude Agent SDK, not through
Claude Code's interactive terminal.

Projector's panel is a Claude Code mod in `hooks/register.tsx`. It is built
on the terminal's plugin API: `$.ui` draws the band above the prompt,
`$.prompt` fills and submits answers, and `$.tool.register` adds the `ask`,
`tell`, and `request` tools. The panel is also its session's deliverer: each
tick it renews the session as `mod`, claims every inbox line the session is
owed, takes its pull request events, and submits them as a prompt. A Paseo
agent has no terminal, so we expect none of this to exist there (§ 7 tracks
confirming it):

- **Nobody sees the agent's items.** An agent can still post with
  `project inbox ask`, but only `project console`, in a terminal on the
  daemon's machine, shows them. A Paseo user is often on their phone.
- **Nothing brings an answer in.** `watch.delivery()` reports `none` for the
  session, so an answer recorded from the console waits until the agent
  happens to poll `project inbox watch --once`. Pull request events
  subscribed with `project watch add` pile up in the same way.

Answering is the inbox's whole point. An item nobody sees, or an answer
that never arrives, stalls the agent's loop on that pull request, often for
hours. The inbox plan deferred "views for Codex and Paseo" until a
session's ID outside Claude Code was settled (`../inbox/readme.md` § 7).
Codex delivery has since settled it for Codex, and § 2.4 settles it for
Paseo.

## 2. Solution

Add a Paseo plugin in `paseo/` at the repository root, packaged as
[docket](https://github.com/jmfieldman/docket)'s Paseo plugin is. Its app
side draws Projector's band as a pill on each agent's composer track, whose
popover lists the agent's open items with their drawers. It also draws the
full panel (pull requests and items) as an agent panel, and adds a
badge with open counts to each workspace row. Its daemon side maps each
Paseo agent to its Projector session through the agent's
`persistence.nativeHandle`. It runs the `project` CLI for every read and
every write, so the inbox store keeps one writer. It also becomes the
session's deliverer, under a new deliverer value `paseo`. Every 5 seconds,
and when an agent's turn ends, it takes the session's pull request events,
claims the inbox lines the session is owed, sends them in one prompt with
`agents.ref(id).send`, and marks them delivered. Agents keep posting
through `project inbox`. The plugin needs no MCP server and changes nothing
in Claude Code or Codex.

### 2.1 The flow

1. An agent in Paseo posts an item with `project inbox ask`, `tell`, or
   `request`. The CLI finds the session in `--session`,
   `PROJECTOR_SESSION`, or `CLAUDE_CODE_SESSION_ID`, as it does today.
2. On its tick, the daemon side lists Paseo's agents, maps each one to its
   session (§ 2.4), and runs one `project inbox list --json` for every
   session's open items. It caches the result and serves it to the app
   through the RPC `projector.items`.
3. The app shows the item as a pill on that agent's composer track, as
   § 2.2 describes. You open the pill's popover and then the item's drawer,
   and you press an option, Discuss, dismiss, or snooze.
4. The app calls the RPC `projector.act`, and the daemon side runs the
   matching command with the agent's session: `project inbox answer`,
   `done`, `decline`, `unclear`, `start`, `discuss`, `dismiss`, `snooze`, or
   `restore`. These are the same commands the console runs. The item shows
   `sending`.
5. The deliverer (§ 2.3) claims the line on its next tick, or as soon as the
   agent's turn ends, sends it as a prompt, and marks it delivered. The
   agent hears `ANSWER 3.a …` as a new turn, exactly as it does in Claude
   Code.
6. An answered item leaves the popover 30 seconds later, as it does in the
   band. A message you send with Discuss reaches the agent as a `DISCUSS`
   line, and the agent answers in its own conversation, which Paseo already
   shows.

### 2.2 Surfaces

| Contribution | Paseo API | What it shows |
| --- | --- | --- |
| Band pill | `addComposerPill`, one per agent with a session | `Projector`, then the counts of open questions, requests, and notices, in the panel header's order. Its popover lists each open item on one line and expands an item into its drawer: detail, options, and `discuss \| dismiss \| snooze`. Paseo opens the popover above the pill, and as a bottom sheet on a phone. |
| Panel | `addAgentPanel`, in the Explorer and as a tab | The full panel for `params.agentId`: pull requests by section, as `project console` shows them, then items. Discuss on a pull request, or on the session, records a `message` item, as the console does. |
| Workspace badge | `addWorkspaceRowBadgeItem` | The open count across the workspace's agents, in tone `warning` while any question or request is open. One shared query serves every row. |
| Command Center | `addCommandCenterItem` | `Projector: Open panel` for the focused agent. |

The pill track keeps its height whether or not it holds pills, so a pill
appearing never moves the transcript. An agent with no open item and no
subscription gets no pill.

### 2.3 Delivery

The daemon side is the deliverer for each mapped session that no other
deliverer renews. It never takes over a session that `mod`, `stream`, or
`codex` renews, so a session never has two deliverers. Each tick, for each
mapped session:

1. It runs `project watch renew --deliverer paseo --if-free`, which renews
   the session as `paseo` and exits 0, or exits 65 and changes nothing when
   `watch.delivery()` reports another deliverer. On 65 the plugin still
   shows the session's items but delivers nothing for it this tick.
2. When the agent's status is `running` or `initializing`, it stops there,
   because `send` fails while a turn is active.
3. It runs `project inbox owed --json` (§ 2.5) and `project inbox claim`
   for each owed line, then `project watch take` for the session's events.
4. It sends the claimed lines and the events as one prompt, built as the
   panel's `deliver` in `hooks/register.tsx` builds it, with
   `agents.ref(agentId).send`.
5. When the send succeeds, it runs `project inbox delivered --claim` for
   each line. When it fails, it keeps the claims, renews them with
   `claim --renew`, and keeps the taken events in memory to send on the next
   try. It never runs `reopen` for a failed send, because `reopen` deletes
   your answer.

`server.on("agent.turn_ended")` runs the same steps for that agent half a
second after the turn ends, as docket's runner does, so an answer recorded
mid-turn arrives as the next turn rather than up to 5 seconds later. When
the plugin stops or reloads, its claims lapse within a minute, and its
in-memory events are lost (§ 7).

### 2.4 Mapping an agent to its session

| Paseo agent `provider` | Projector session |
| --- | --- |
| `claude` or `claude/*` | `persistence.nativeHandle`, else `persistence.sessionId`: the Claude Code session ID, the same value Claude Code passes as `CLAUDE_CODE_SESSION_ID` |
| `codex` or `codex/*` | `persistence.nativeHandle`: the Codex thread ID, which `codex_watch.py` uses as the session |
| Anything else | None. The agent gets no pill, and the plugin does not deliver to it. |

An agent archived, closed, or in `error` keeps its items in the store. The
plugin stops renewing its session and shows its items only while the agent
is listed.

### 2.5 Changes by file

| File | Change |
| --- | --- |
| `paseo/paseo-plugin.json` | Plugin ID `projector`, requiring Paseo `>=0.13.0`, the first release with agent panels. |
| `paseo/index.server.ts` | Registers the RPCs, the 5-second tick, and the `agent.turn_ended` and `agent.archived` handlers. Widens `PATH` so the desktop app's daemon finds `project`, `git`, and `gh`. |
| `paseo/server/cli.ts` | Runs `project` with arguments, never through a shell, and parses its JSON. |
| `paseo/server/deliverer.ts` | § 2.3, with its claims and pending events per agent. |
| `paseo/server/sessions.ts` | § 2.4. |
| `paseo/shared/rpc.ts` | `projector.items`, `projector.rows`, and `projector.act` contracts, in Zod. |
| `paseo/index.client.tsx`, `paseo/client/*.tsx` | The surfaces in § 2.2, styled with `theme.colors` so they work in light and dark themes. |
| `paseo/test/*.test.ts` | The deliverer and session mapping against a fake Paseo API and a fake `project`. |
| `src/projector/watch.py` | Adds `paseo` to `DELIVERERS`, and a `renew` mode that refuses while another deliverer's renewal is live. |
| `src/projector/cli.py` | Adds `--if-free` to `project watch renew`. Adds `project inbox owed [--session] --json`, which prints each owed line's reference and text from `inbox.undelivered`, for every session when no session is given. Adds `project watch rows --session --json`, which prints `rows.rows_for`, the pull request rows the console shows. |
| `.github/workflows/check.yml` | Runs `npm ci`, the type check, and the tests in `paseo/`. |
| `docs/cli.md` | Documents `owed`, `watch rows`, `renew --if-free`, and the `paseo` deliverer, including `events: paseo` in `project watch add`. |
| `paseo/README.md` | Installing, enabling plugins, and developing, as docket's README does. |

## 3. Why this design

- **The CLI, not a port of the store.** Docket bundles its TypeScript
  action layer into its plugin. Projector's core is Python, and its inbox
  functions take a lock and rename files into place. A second
  implementation in TypeScript would duplicate that and drift from it.
  Calling `project` keeps one writer, as the inbox plan requires.
- **Record, then deliver, as the console does.** The band answers by
  filling the prompt, and its `prompt.submit` hook records the answer. A
  Paseo composer item can set the prompt but cannot submit it, and on a
  phone you would have to find the composer to send. Recording through the
  CLI and letting the deliverer send works from every surface, including
  the badge's workspace and a closed panel.
- **The plugin delivers, not the watch daemon.** The watch daemon would
  need Paseo's WebSocket client, its relay or Tailscale credentials, and a
  Node runtime. The plugin already runs inside Paseo's daemon with an
  authenticated SDK, and docket's runner shows that `send` and
  `turn_ended` are enough.
- **A new deliverer value, not `stream`.** `stream` means a running
  `project watch stream` reads the events, and the `SessionStart` context
  tells the agent what to run for each value. A distinct `paseo` lets that
  context say that Paseo delivers, so the agent runs no watcher, and lets
  `project agent list` name the host.
- **A pill and a panel, not a panel alone.** The pill is where the band
  is: beside the agent, in view while you talk to it. A panel alone is a tab
  you have to open, which is how an item goes unseen. The panel holds what
  is too large for a popover: the pull request rows.
- **Not `register.tsx` under React Native.** The mod renders through
  Claude Code's terminal components and calls `$.*` throughout. The shared
  parts are already data: `rows.py` for pull request rows and the inbox
  JSON for items. The plugin draws those with Paseo's components.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| A Claude agent in Paseo runs `project inbox ask "Ship it?" --recommend "Yes"` | A `Projector 1` pill appears on that agent's composer within 5 seconds, on desktop and on the phone app. |
| Press the recommended option in the popover | The agent's next turn starts with `ANSWER <ref>.a Ship it? → Yes`, and `project inbox list` shows the item delivered. |
| Answer while the agent is mid-turn | The answer arrives as the turn after the current one ends, not as an error and not twice. |
| Answer from `project console` instead | The same agent hears it, through the plugin. |
| `project watch add --json` a pull request, then push a review to it | `add` reports `"delivery": "paseo"`, and the agent hears the event as a turn. |
| Run a session in Claude Code's terminal, where the mod renews it | The plugin neither renews nor delivers for that session: the agent hears each answer once. |
| Make `send` fail, such as by stopping the agent | The answer stays claimed and is sent when the agent is usable, or the claim lapses to the next deliverer. No answer is deleted. |
| Disable the plugin | Within 5 minutes, `project watch add --json` reports `"delivery": "none"` for its sessions, and answers wait for the next deliverer. |

Tests that fail before the change:

- `paseo/test/deliverer.test.ts`: a send that throws "turn is already
  active" leaves every line claimed and never runs `reopen`. This catches a
  deliverer that loses answers when it races a turn. Another test gives a
  session that `mod` renews and expects no `claim` call, which catches two
  deliverers sending the same answer.
- `paseo/test/sessions.test.ts`: a Claude agent whose `nativeHandle` is
  unset maps to `persistence.sessionId`, and a `pi` agent maps to none.
- `tests/test_inbox.py`: `project inbox owed --json` lists a claimed line
  only after its claim lapses, the same rule `undelivered` enforces.
- `tests/test_watch.py`: `renew --deliverer paseo --if-free` exits 65 and
  leaves `session.json` unchanged while `mod` renewed the session within the
  lapse, and renews once that renewal has lapsed. This catches a plugin
  taking a session from a running Claude Code panel.

## 5. Cost

- **CLI processes.** Each tick starts one `project inbox list --json` and
  one `project inbox owed --json` for every session, plus one
  `project watch renew` for each session the plugin delivers. Each Python
  start costs roughly 50 to 100 ms. Measure 10 agents at under 3% of one
  CPU core. If the measurement fails, batch the renewals into one command.
- **Pull request rows.** `project watch rows` runs only while an agent's
  panel is open, every 5 seconds, and reads the watch daemon's cache. It
  costs no GraphQL.
- **Phone clients.** Each client fetches `projector.items` once per tick.
  The plugin serves it from the tick's cache, so clients add no CLI
  processes.

## 6. Rollout

1. **Spike.** Run a Claude agent and a Codex agent in Paseo, and record the
   answers to the first two open questions in § 7, with evidence, in this
   plan's § 8. Move the status to `ready` once they are settled.
2. **CLI.** `paseo` in `DELIVERERS`, `renew --if-free`,
   `project inbox owed`, `project watch rows`, their tests, and
   `docs/cli.md`. Each works alone.
3. **Plugin.** `paseo/`, its tests, the Check workflow step, and its
   README.

## 7. Open questions

- **Does Projector's mod load in a Paseo Claude agent, and does its shell
  get `CLAUDE_CODE_SESSION_ID`?** Docket's skills load there from your
  Claude settings, but a skill is not a mod. If the mod loads, it renews
  the session as `mod`, the plugin stays out of its way (§ 2.3), and only
  the surfaces in § 2.2 are new. If the variable is missing, the agent
  cannot name its session to `project inbox`. Owner: ninjudd, settled by
  rollout step 1.
- **Does a Paseo Codex agent register for Codex delivery?** If Paseo runs
  Codex with Projector's Codex hooks, the adapter would call
  `codex queue --thread` for a thread that Paseo's own connection drives.
  Until the spike settles this, the plugin shows Codex agents' items but
  delivers only while their delivery is `none`. Owner: ninjudd.
- **Events lost on reload.** `watch take` clears the events before the
  send, so a plugin reload between the take and the send loses them.
  Deferred: the agent rechecks its subscriptions on its next event, and a
  later `watch take --peek` or re-append command can close the gap.
- **Notifications.** Paseo's plugin API has no notification call. Deferred
  until it has one; the badge and the pill carry the signal until then.
- **Distribution.** Install from Git (`paseo plugin install
  git:ninjudd/projector:paseo`) at first. Listing it in Paseo's registry is
  deferred until the plugin is in use.

## 8. Background

### 8.1 What Paseo offers

Read from paseo.sh's documentation on 2026-11-17:

- **Plugins** have an app entry, `index.client.tsx`, in React Native, and
  a daemon entry, `index.server.ts`, that runs in a Node subprocess with the
  daemon user's access. They talk through RPC contracts defined in
  `shared/`. Plugins are trusted and unsandboxed, and they run only after
  you turn on **Enable plugins** in Settings.
- **Surfaces** include sidebar items, workspace and agent panels, Command
  Center items, slash commands, composer items, composer pills, timeline
  renderers, and workspace row badges. The composer pill track is the only
  surface above the composer. No banner, drawer, or notification API
  exists.
- **Composer items** can set or insert the prompt text, but nothing the
  documentation lists submits it.
- **The SDK** offers `agents.ref(id).send(prompt)` for fire-and-forget
  delivery and `run(prompt)` to await a turn. An agent snapshot carries
  `status` (`initializing`, `idle`, `running`, `error`, or `closed`) and
  `requiresAttention`.
- **Claude Code** runs through the `claude` CLI and the Claude Agent SDK,
  with the account already signed in on the machine.

### 8.2 Docket's plugin

Docket's `paseo/` directory is a working Paseo plugin for a Claude Code
plugin's state, and this plan follows its layout. Its runner reads the
Claude session ID as `persistence.nativeHandle ?? persistence.sessionId`,
sends with `agents.ref(id).send`, treats an error matching "turn is
already active" as busy, and holds prompts until `agent.turn_ended` or a
5-second flush. Its README reports that Paseo's Claude agents load Claude
Code plugins from your Claude settings, so Projector's skills load there.
