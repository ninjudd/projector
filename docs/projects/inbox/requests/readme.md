---
status: ready
priority: now
---

# Ask the person to do what only they can

An agent can put a request in your inbox for work only you can do, such as
testing a build on your phone, and the request carries the whole hand-off,
from the agent's preparation to your result, as one item.

## 1. Problem

Projector's inbox holds two kinds of item. A question asks you to decide
something, and your answer goes back to the agent that asked. A notice tells
you something and sends nothing back. Both live in a store kept for each
session, and Projector's panel, above the Claude Code prompt, lists them.

Neither kind fits a third exchange that keeps coming up: the agent needs you
to do something it can't do itself. Examples are testing a build on your
phone, running a command the agent isn't allowed to run, and signing in to a
service. An agent handles these today with a question, a notice, and prose in
the chat. None of those says when you're ready, when the agent's preparation
is done, or what the result was, so you and the agent coordinate by hand.

In one Fyra session, a change that only a thumb on a real iPhone could check
took four exchanges: a question asking whether to install the build, the
install, a notice with the test steps, and a typed message with the result.
An earlier install had failed because the phone was locked, which the agent
could report only in another message.

## 2. Solution

Add a third kind of item, a request. An agent posts one with `project inbox
request`, or the `request` tool in Claude Code. It has a one-line subject, its
steps as Markdown detail, and, optionally, a start action: a one-line
description of what the agent does when you're ready, such as "Install build
`c0d62a3b` on your iPhone and launch it." A request without a start action
waits for you to do it and then press **done** or **decline**. A request with
one first shows **start**. Pressing it sends the agent `STARTED`, quoting the
start action, and is your consent to that action and nothing more. The agent
does it, then marks the request ready, or blocked with a reason, such as "The
phone is locked. Unlock it and press retry." **done** and **decline** put the
quoted request in your prompt with `Done` or `Declined` under it, so you can
add notes and send them. Build it in two layers: layer 1 is a request with
done and decline, and layer 2 adds the start action.

### 2.1 The flow

1. An agent posts a request: `project inbox request <request> --detail
   <steps> [--start <action>]`, or the `request` tool. It takes the next
   number in the session's one count, as questions and notices do, and the
   panel lists it with them.
2. You open its drawer: the steps, then the actions. A request without a
   start action, or one that is ready, shows `done | decline | discuss |
   snooze`. A request with a start action shows **start** before them.
3. **start** runs `project inbox start <number>`, which records the start
   action as it reads at that moment. The panel's deliverer then claims the
   line it owes and sends `STARTED 5 <request> → <start action>`, to the
   subagent that posted the request while it runs, or otherwise to the main
   loop, exactly as it delivers `DISMISSED` today. When the delivery fails,
   `reopen` removes the start, a toast says why, and **start** comes back.
4. While the agent works, the request's line says `starting`, and its drawer
   shows the start action in place of **start**.
5. The agent finishes and runs `project inbox ready <number>`, through the
   `ready` tool. The line says `ready`. If it can't finish, it runs `project
   inbox block <number> --reason <line>`, through the `block` tool. The line
   says `blocked`, the drawer shows the reason, and **start** returns as
   **retry**, which records a new start as step 3 does.
6. **done** puts `"<request>"`, a blank line, `Done`, and a blank line in the
   prompt, with the cursor at the end. **decline** does the same with
   `Declined`. You type any notes and send the prompt, or send it as it
   stands.
7. The panel's `prompt.submit` hook reads a quoted open request whose next
   line that isn't blank is exactly `Done` or `Declined` as that outcome. The
   rest of the text, up to the next quote, is your notes. The hook runs
   `project inbox done` or `decline`, then `claim` and `delivered`, because
   the prompt itself carried the outcome and your notes to the main loop. When
   a subagent posted the request, the hook names it in the prompt's
   `context`, as it does for a question. A quoted request followed by any
   other text replies to it and leaves it open, as a reply to a question does.
8. From a terminal, `project inbox done <number> [--text <line>]` and
   `decline` record the outcome, and the deliverer sends `DONE 5 <request> →
   <text>` or `DECLINED 5 <request> → <text>`, without the arrow and text when
   none was given.
9. The agent can `revise` an open request's subject, steps, or start action,
   and `withdraw` it, as it can a question. Revising a request that was
   started, ready, or blocked returns it to open, so the new start action
   needs a new **start**. You can snooze a request, and restore one you
   snoozed or declined, as you can a question.

Layer 1 is steps 1, 2, and 6 to 9 without a start action. Layer 2 adds
`--start`, steps 3 to 5, and the reset in step 9.

### 2.2 The store

A request is a file in the session's directory beside its questions and
notices, with these fields.

| Field | Value |
| --- | --- |
| `kind` | `request` |
| `ref`, `number`, `pr`, `agentId`, `postedAt`, `detail` | As for any item; `detail` holds the steps |
| `subject` | The request, in one line of at most 120 characters |
| `start` | Layer 2: the start action, one line of at most 120 characters; absent when there is nothing to prepare |
| `started` | Layer 2: `{ action, startedAt }`, the start action as it read when you pressed **start** or **retry**, and when. `deliveredAt` is added once `STARTED` is delivered. Removed by `revise` and by a failed delivery |
| `progress` | Layer 2: `{ state, reason, at }`, where `state` is `ready` or `blocked` and `reason` is given only for `blocked`. Removed by `start` and `revise` |
| `answer` | Done: `{ done: true, text, answeredAt }`. Declined: `{ dismissed: true, declined: true, text, answeredAt }`. Withdrawn: as for any item. `text` is absent when none was given |
| `claim`, `deliveredAt`, `snoozedUntil`, `snoozedAt`, `restoredAt` | As for a question |

A request owes its agent a line when it has an undelivered `answer`, a
`restoredAt`, or a `started` without `deliveredAt`. `claim` takes the first
of those, and `settle` marks it delivered or undoes it: a failed `STARTED`
removes `started`. Recording `done` or `decline` removes a `started` that
was not yet delivered, and its claim, because the outcome replaces it.

`project inbox list` prints a request's state as `open`, `started`,
`ready`, `blocked`, `done`, `declined`, or `withdrawn`, from the fields
above in reverse order of that list.

### 2.3 The CLI

| Command | Change |
| --- | --- |
| `project inbox request <request> [--detail <markdown> \| --detail-file <path>] [--start <action>] [--pr <owner/repo#number>] [--agent <id>]` | New. Checks the subject as `tell` does, and `--start` by the same rules. Prints the number |
| `project inbox done <number> [--text <line>]` | New. Records the outcome; exits `65` for an item that isn't an open request |
| `project inbox decline <number> [--text <line>]` | New. As `done`, recording a decline |
| `project inbox start <number>` | Layer 2, new. Exits `65` for a request with no start action, one already started and not blocked, and a resolved one |
| `project inbox ready <number>` | Layer 2, new. Exits `65` unless the request is started |
| `project inbox block <number> --reason <line>` | Layer 2, new. As `ready`, recording the reason |
| `project inbox dismiss <number>` | A request records a decline with no text |
| `project inbox answer <number>` | Exits `65` for a request, naming `done` and `decline` |
| `project inbox revise <number> [--request <line>] [--detail <markdown>] [--start <action>]` | Takes a request; `--question`, `--recommend`, and `--option` exit `65` for one. Layer 2 resets a started, ready, or blocked request to open |
| `show`, `context`, `claim`, `delivered`, `reopen`, `watch` | Take a request. `context` prints `<number> is the request: <subject>` |

`start`, `done`, and `decline` record what only you may decide, so they join
`answer`, `dismiss`, and `snooze` among the commands Projector's permission
rules make an agent ask before running. `request`, `ready`, and `block` join
the commands an agent runs freely.

### 2.4 Claude Code: the tools

The hooks module registers `request`, and in layer 2 `ready` and `block`,
beside `ask` and `tell`, with `isDeferred: false`.

- **`request`** takes `request`, `detail`, `pr`, and in layer 2 `start`. Its
  description tells the agent to use it for work only the person can do, to
  put the steps in `detail`, and to name what it will do when the person
  starts in `start`. It tells the agent to do the start action only after a
  message that starts `STARTED <number>` arrives, and to do exactly what that
  message quotes. It answers `Requested 5.` and the messages to expect:
  `DONE 5` or `DECLINED 5`, and `STARTED 5` first when there is a start
  action.
- **`ready`** takes `number`. **`block`** takes `number` and `reason`, the
  line the person reads, which says what they need to do before pressing
  retry.
- **`revise`** takes `request` and `start` for a request, beside `detail`.
  **`withdraw`** and **`restore`** take a request unchanged.
- **`ask`** and **`tell`** add one sentence to their descriptions: when the
  person needs to do something, use `request`.

A refusal follows `ask`'s rules: exit `65` gives the check that failed.

### 2.5 Files

| File | Change |
| --- | --- |
| `src/projector/inbox.py` | Add `request` to `KINDS`; add `request`, `done`, `decline`, and in layer 2 `start`, `ready`, and `block`. Extend `dismiss`, `revise`, `claim`, `settle`, `message`, `resolution`, `owes_a_line`, and the kind checks |
| `src/projector/cli.py` | The subcommands and flags in § 2.3, the request's states in `list`, and its `show` and `context` text |
| `src/projector/permissions.py` | `start`, `done`, and `decline` among the ask rules; `request`, `ready`, and `block` among the allowed commands |
| `hooks/types.d.ts` | A `Request` type in `Item` |
| `hooks/inbox.ts` | `needsDelivery` for a request's owed lines; `readPrompt` reads `Done` and `Declined` under a quoted request; the tools' descriptions and argument builders |
| `hooks/register.tsx` | The tools; the request's line, color, and state mark; its drawer's actions; `act` runs `start`; `deliver` and the prompt hook take a request |
| `tests/test_inbox.py`, `tests/test_permissions.py`, `hooks/inbox.test.ts`, `hooks/panel.test.tsx` | Tests for the criteria in § 4 |
| `docs/cli.md` | "Use the inbox" describes requests and the new commands |
| `docs/plugins.md` | "Read your inbox in Projector's panel" describes a request's drawer and the tools |

## 3. Why this design

- **A third kind in the same store.** A request lives the life questions and
  notices do: an agent posts it, it waits in one list, and you resolve it.
  Keeping it in the session's store reuses the numbering, the lock, the
  panel's reading of the store, its drawer, snoozing, withdrawing, restoring,
  and delivery. A separate store would duplicate all of them.
- **Not a question with options.** A question with "ready, install it" and
  "not now" covers the moment you say go and nothing after it, because an
  answer closes the question. The steps and the result then travel as
  separate notices and messages, which is the problem.
- **`started` follows `restoredAt`.** Today an answer closes its item, and
  `restoredAt` is the one line an item can owe its agent while it stays open.
  `started` is a second such line, claimed, delivered, and undone by the same
  `claim` and `settle`. A list of events for every item was considered and
  rejected: only a request needs a line that leaves it open, and a list would
  change every kind's record for one kind's need.
- **A decline is a dismissal with a reason.** Storing it as `dismissed` with
  `declined` lets `restore`, `resolution`, and the panel's handling of a
  dismissed item work without new branches. A request's drawer shows
  **decline** in place of **dismiss**, because two ways to say no to the same
  request would mean the same thing.
- **start is a button; done and decline fill the prompt.** start carries
  nothing but consent, so it records the event directly, as **dismiss** does,
  and the deliverer sends the line. A prompt would cost you an Enter and
  would let the quoted start action be edited on its way. done and decline
  usually carry notes, which you type in the prompt, so they follow a
  question's answer.
- **Exactly `Done` or `Declined`.** `readPrompt` takes a question's answer
  only when the text after its quote is exactly one of its options. A
  request takes its outcome only when the first line under the quote is
  exactly the word, so a reply that happens to begin "done?" stays a reply.
- **Consent covers the start action as it read.** `STARTED` quotes the text
  recorded when you pressed start, and revising a started request returns it
  to open, so a press for one build never covers installing another. Making
  `start`, `done`, and `decline` ask rules keeps an agent from recording
  your consent itself.
- **Agent events are writes, not messages.** ready and blocked change what
  the panel shows you and send nothing back, because the agent that writes
  them already knows.
- **Two layers.** Layer 1 already serves a request with nothing to prepare,
  such as running a command or signing in, and it sets the kind, the drawer,
  and the prompt's reading in place. Layer 2 adds the start's lifecycle on
  top, and each layer's review stays about one thing.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| `project inbox request "Test the gutters" --detail "1. …"` | Prints the next number; `list` shows it `open` beside questions and notices |
| A subject of two lines or 121 characters | Exits `65`, as `tell` does |
| Press **done**, add a line of notes, send | The request is `done` and delivered once; the agent reads the notes in the prompt; no `DONE` line follows |
| Send the quote with `done?` under it | A reply: the request stays open |
| `project inbox decline 5 --text "No phone today"` | The deliverer sends `DECLINED 5 Test the gutters → No phone today` once |
| `project inbox answer 5.a` on a request | Exits `65`, naming `done` and `decline` |
| `restore` a declined request | It is open in place; an agent that heard `DECLINED` hears `RESTORED` |
| Layer 2: press **start** | `STARTED 5 Test the gutters → Install build c0d62a3b…` reaches the subagent while it runs, else the main loop; the line says `starting` |
| Layer 2: a failed `STARTED` submit | `started` is removed, a toast says why, and **start** returns |
| Layer 2: `project inbox block 5 --reason "Unlock the phone"`, then **retry** | The drawer shows the reason; retry records a new `started` and removes `progress` |
| Layer 2: `revise 5 --start "Install build d41f…"` after start | The request is open, and only a new press sends `STARTED` with the new action |
| Layer 2: `start` on a request with no start action, or twice before ready | Exits `65` |
| An agent's `project inbox start`, `done`, or `decline` | Prompts for permission under Projector's rules; `request`, `ready`, and `block` do not |
| Press **done** before a pending `STARTED` is delivered | The agent hears the outcome only |

Tests in `tests/test_inbox.py` and `hooks/inbox.test.ts` cover each row in
temporary stores, and `tests/test_permissions.py` covers the rules. They fail
before the change because the `request` kind and its commands don't exist.

## 5. Cost

The panel's tick reads the store only when a file changes, and a request
adds no file and no process to an idle tick. Each new tool adds its
description to every Claude Code session's tool list: `request`'s about as
long as `ask`'s, and `ready`'s and `block`'s a sentence or two each. Nothing else is on a hot
path.

## 6. Rollout

1. Layer 1 ships the `request` kind with done and decline, with docs and
   tests, as one pull request.
2. Layer 2 ships the start action on top of it, as the next layer of a
   `gh stack`, and can merge with layer 1 or after it.

## 7. Open questions

- **The request's look.** The line's color, the `starting`, `ready`, and
  `blocked` marks, and the wording of the drawer's start action are a first
  choice. Owner: ninjudd, who adjusts the panel in daily use, as § 7 of the
  [inbox](../readme.md) plan records for questions and notices.
