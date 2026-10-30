---
status: completed
priority: now
---

# Tell an agent you don't understand an inbox item

One press in an inbox item's drawer tells the agent that posted the item
that you don't understand it, and the agent revises the item in place to
explain it more simply or to add the context you lack.

## 1. Problem

Projector's inbox holds three kinds of item that agents post for you: a
question to answer, a notice to read, and a request for work only you can
do. Projector's panel, above the Claude Code prompt, lists them. Each
item's drawer shows its detail and the actions you can take on it.

An agent writes an item from inside the work it just did, with that work's
identifiers and shorthand, so an item can be clear to the agent and opaque
to you. Today the only way to say so is **discuss**, which puts the quoted
item in your prompt for you to type what confused you and send. That costs
you a sentence when all you mean is "I don't follow", and for a notice it
fails twice over:

- **A notice cannot be revised.** `project inbox revise` refuses a notice
  and tells the agent to withdraw it and tell again, so any explanation
  arrives as a new item, and the one that confused you disappears.
- **A reply to a notice never reaches a subagent.** The panel reads a
  quoted question or request in a sent prompt as a reply, and names the
  subagent that posted it so the main loop passes the reply on. It reads no
  reply to a notice, so word about a subagent's notice reaches only the main
  loop, which did not write it.

## 2. Solution

Add an action, **I don't understand**, on `6` in the drawer of every open
item. One press runs `project inbox unclear`, which records `unclear` on the
item, and the panel delivers `UNCLEAR <number> <subject>` to the agent that
posted it: the subagent while it runs, otherwise the main loop, exactly as
it delivers `DISMISSED` and `STARTED`. The item's line reads `unclear`
until the agent revises it. Notices become revisable, so the agent revises
any kind of item in place, keeping its number, and the revision clears the
mark. A question offers at most five choices, its recommendation and four
others. A request offers at most four options, so with **Start** or
**Retry** on `1` its choices also end at `5`. That leaves `6` free in every
drawer. To say what you don't understand, you still use **discuss**.

### 2.1 The flow

1. You open an open item's drawer. Under its detail and any choices, its
   actions read:

   ```text
   6: I don't understand this question
   7: discuss question
   8: dismiss question
   9: snooze question
   ```

   The label ends with the item's kind, `question`, `notice`, or
   `request`, as every action's does. A resolved item's drawer has no such
   line.
2. You press **I don't understand**, by its digit, a click, or Enter. The
   panel runs `project inbox unclear <number>` with the subject and detail
   the drawer shows, and closes the drawer. The item's line ends with
   `unclear`, in `warning`, and its drawer no longer offers **I don't
   understand**. If the agent revised the subject or detail since the panel
   drew the drawer, `unclear` records nothing, as start records nothing for
   a revised start action, and the drawer stays open on the new text.
3. The panel's deliverer claims the line the item owes and sends
   `UNCLEAR 5 <subject>` to the subagent that posted the item while it
   runs, or otherwise submits it to the main loop as a prompt. When the
   main loop receives a subagent's line, a note follows it: `The subagent
   <description> (<id>) posted this, and the message could not reach it;
   send it this message, or revise the item yourself.` From a terminal,
   `project inbox watch` sends the same line.
4. The agent revises the item with the `revise` tool: a simpler subject, a
   detail with the missing context, or both. The item keeps its number and
   its place in the list, and the revision removes `unclear`, so the line
   drops its mark and the drawer offers **I don't understand** again for
   the new text.
5. When the delivery fails, the panel removes `unclear`, and a toast says
   `projector: word that you don't understand "<subject>" did not reach its
   agent (<reason>); press I don't understand again`. The action returns.

Your other actions work throughout. An answer, outcome, or dismissal
recorded before any deliverer claimed the `UNCLEAR` removes `unclear`,
because you no longer need the explanation, so the agent hears only the
answer, outcome, or dismissal. A claimed `UNCLEAR` may already wait in the
session's queue, so it keeps its claim and reaches the agent first, as a
claimed `STARTED` does.

An item stored before this change can hold a choice on `6`: a question
with six choices, or a request with five options and **Start** or
**Retry** on `1`. Its drawer keeps each choice on its digit and draws
**I don't understand** without a digit, for a click or Enter.

### 2.2 The store

An item gains one field, beside the fields that the
[requests](../requests/readme.md) plan's § 2.2 lists.

| Field | Value |
| --- | --- |
| `unclear` | `{ at }`, when you pressed **I don't understand**. `deliveredAt` is added once `UNCLEAR` is delivered. Removed by `revise`, by a failed delivery, and by an answer, outcome, or dismissal recorded before any deliverer claimed the line |
| `claim.line` | Takes `unclear` beside `answer`, `restored`, and `started` |

An open item owes its agent the `unclear` line while it has `unclear`
without `deliveredAt`, after any answer, `RESTORED`, or `STARTED` line it
owes. That holds for a notice too, for which `unclear` is the only line it
can owe. `claim` and `settle` treat the `unclear` line as they treat
`started`: a delivered line records `deliveredAt`, a failed one removes the
field, and a renewal keeps a claimed line's claim even once the item is
resolved.

`project inbox list` prints an open item that has `unclear` as `unclear`,
ahead of a request's `started`, `ready`, or `blocked`.

### 2.3 The CLI

| Command | Change |
| --- | --- |
| `project inbox unclear <number> [--subject <line>] [--detail <markdown>]` | New. Records `unclear` on an open item of any kind. `--subject` and `--detail` give the text the person read, and `unclear` exits `65` when either differs from the stored text, as `start` does when its `--action` differs from the stored start action. Also exits `65` for a resolved item, and for an item already marked unclear that its agent has not revised since |
| `project inbox revise <number> [--notice <line>] [--detail <markdown>]` | Takes a notice, with its new subject in `--notice`, checked as `tell` checks one; `--question`, `--request`, `--start`, `--recommend`, `--option`, and `--no-options` exit `65` for a notice. Revising any item removes its `unclear` and drops a claim on that line |
| `project inbox ask`, `request`, `revise` | `--option` takes at most four: a question's other answers, or a request's options. A fifth exits `65` |
| `claim`, `delivered`, `reopen`, `watch` | Take a notice that owes the `unclear` line |

`unclear` records what only you may say, so it joins `answer`, `dismiss`,
`snooze`, `done`, `decline`, and `start` among the commands that
Projector's permission rules make an agent ask before it runs them.

### 2.4 Claude Code: the tools

- **`ask`**, **`tell`**, and **`request`** each add a sentence to their
  descriptions: when the person presses I don't understand, a message that
  starts `UNCLEAR 3` arrives, and the agent revises the item to explain it
  more simply or to add the context the person lacks, keeping its number.
  Each tool's result names `UNCLEAR` beside the other messages it names.
- **`ask`** and **`request`** each take at most four items in `options`,
  and each schema's description of `options` says four where it says five.
- **`revise`** takes a notice, with a new `notice` field for its subject,
  and its `options` takes at most four items, for a question or a request.
  Its description names a notice beside a question and a request, and says
  that a revision clears the person's I don't understand.

### 2.5 Files

| File | Change |
| --- | --- |
| `src/projector/inbox.py` | `MAX_OPTIONS = 4`, the cap that `check` applies to a question's other answers and `check_request_options` to a request's options; a new `unclear`, which compares the subject and detail it is given with the stored ones; `owed_line`, `claimed_line`, `message`, `claim`, `settle`, `clear_for_outcome`, and `revise` handle the `unclear` line and notices |
| `src/projector/cli.py` | The `unclear` subcommand with `--subject` and `--detail`, `revise --notice`, the `--option` help of `ask`, `request`, and `revise`, and the `unclear` state in `item_state` |
| `src/projector/permissions.py` | `Bash(project inbox unclear *)` in `ASK`, and `unclear` in `ASKED`, in the module docstring's two lists of the commands that record the person's choice, and in the `CODEX_RULES` comment that `project init` writes to the Codex rules file |
| `hooks/types.d.ts` | `unclear?: { at: number; deliveredAt?: number }` on `Posted` |
| `hooks/inbox.ts` | `needsDelivery` takes a notice that owes the `unclear` line; `forMainLoop` words its note for an `UNCLEAR` line; the tools' descriptions and schemas, with `maxItems: 4` on the `options` of `ask`, `request`, and `revise`; `reviseArguments` passes `--notice` |
| `hooks/register.tsx` | The drawer's line on `6`, drawn without a digit when a choice holds `6`; `act` runs `unclear` with the subject and detail its drawer shows, as it passes `start` the start action; the line's `unclear` mark; `deliver` takes any kind of item; `settle`'s toast for a failed `UNCLEAR` |
| `src/projector/templates/agents-block.md` | Teaches `UNCLEAR` beside `ANSWER`, `DONE`, and `DECLINED`. Bump the block's version from 22 to 23, pin version 23's hash in `tests/test_instructions.py` beside 22's, and run `project init` so this repository's `AGENTS.md` and `CLAUDE.md` take the new block |
| `docs/cli.md` | "Use the inbox" describes `unclear`, `revise --notice`, and the cap of four, for a question's other answers and for a request's options, where it says five or a sixth. Its paragraph on `revise` also drops the claim that `revise` refuses `--option` for a request. Every list of the commands that record your choice names `unclear`: in "Adopt a repository", the `permissions.ask` entries, the paragraph on them, and the paragraph on what the Codex rules leave out, and in "Use the inbox", the paragraph on the ask rules |
| `docs/plugins.md` | "Read your inbox in Projector's panel" and "Use the panel from the keyboard" describe the action and the mark, and say that a question's choices and a request's, after any **Start** or **Retry**, end at `5`, where they say the options keep `1` to `6` |
| `tests/test_inbox.py`, `tests/test_permissions.py`, `tests/test_instructions.py`, `hooks/inbox.test.ts`, `hooks/panel.test.tsx` | Tests for the criteria in § 4. `tests/test_permissions.py` also adds `unclear` to `CODEX_LEFT_OUT`, since its test of the Codex rules wants each new subcommand allowed by name or deliberately left out |

## 3. Why this design

- **One press, not a prompt.** I don't understand carries nothing but the
  signal, as start carries nothing but your consent, so it records the
  event and the deliverer sends the line. Filling the prompt would cost an
  Enter, and for a notice it has no route back to a subagent. When you can
  say what confuses you, **discuss** already carries your words.
- **The `started` line, extended.** `started` is the line an open request
  owes its agent without being resolved. `unclear` is a second such line,
  claimed, delivered, and undone by the same `claim` and `settle`, so the
  panel's `deliver`, its `track` of a pending submit, and the prompt hook
  that settles a submit carry it unchanged. A list of events on every item
  was rejected for requests, and the reason holds here: one more optional
  field changes no other field of any kind's record.
- **A revision clears the mark.** The revision answers the `UNCLEAR`, so
  the mark lasts exactly as long as you wait for it. A press on the old
  text never carries over to the new text, as a press of start never covers
  a revised start action. A revision after the press clears the mark, and
  a press on a drawer drawn before the revision passes the old subject and
  detail, which `unclear` refuses. Those are the parts that an explanation
  revises.
- **Notices become revisable.** Withdrawing and telling again gives the
  explanation a new number and a new place in the list, so the panel shows
  the confusing notice vanish and an unrelated-looking one arrive. Revising
  in place reuses the code that already revises questions and requests.
- **`6`, by capping a question at five choices and a request at four
  options.** Every digit in a question's drawer is taken: `1` to `6` for
  its choices, `7` to `9` for its actions, and `0` to close it. A request
  with **Start** or **Retry** on `1` and five options takes `1` to `6` the
  same way. A letter key works only while the panel has the keyboard,
  never typed first into an empty prompt as a digit is, so the action would
  be the only line in the drawer without that shortcut. Four answers
  besides the recommendation still give a real choice, as four options do
  for a request, and an agent with more can put the rest in the detail.
  The one cap, `MAX_OPTIONS`, holds for a request without a start action
  too, because a revision can add one.
- **An ask rule for `unclear`.** Like an answer or a dismissal, it records
  what only you may say. Without the rule, an agent could mark its own item
  unclear.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Open the drawer of an open question, notice, and request | Each shows `6: I don't understand this <kind>` above discuss; a resolved item's drawer shows none |
| Press `6` on a running subagent's open notice | `project inbox list` shows it `unclear`; the subagent receives `UNCLEAR 5 <subject>` once; the line ends with `unclear`, and the drawer no longer offers the action |
| Press `6` on an item whose subagent has finished | The main loop receives `UNCLEAR 5 <subject>` as a prompt, with the note naming the subagent |
| `revise 5 --notice "<new line>"` after the press | The notice keeps number 5 and its place, shows the new text without the mark, and offers `6` again |
| A failed `UNCLEAR` delivery | `unclear` is removed, a toast says why, and `6` returns |
| `project inbox unclear 5` twice before a revision, or once on a resolved item | Each exits `65` and records nothing |
| Press `6` on a drawer drawn before `revise` changed the item's subject or detail | `unclear` exits `65` and records nothing, and the drawer stays open on the new text, offering `6` |
| Dismiss or answer an item whose `UNCLEAR` no deliverer claimed | No `UNCLEAR` is sent; the agent hears only the dismissal or the answer |
| Answer an item while its `UNCLEAR` is claimed | The agent hears `UNCLEAR`, then the answer |
| `project inbox ask` with five `--option` flags | Exits `65`, naming the cap of four other options |
| `project inbox request` with five `--option` flags, or `revise` of a request with five | Each exits `65`, naming the cap of four options |
| A question stored with six choices | Its drawer shows all six on `1` to `6`, and I don't understand without a digit |
| A request stored with a start action and five options | Its drawer shows **Start** on `1` and the options on `2` to `6`, and I don't understand without a digit |
| An agent runs `project inbox unclear` | Claude Code asks permission under Projector's rules |
| `project inbox watch --once` with an unclear notice | Prints `UNCLEAR 5 <subject>` and marks the line delivered |

`tests/test_inbox.py` and `hooks/inbox.test.ts` cover the store and CLI
rows in temporary stores. `hooks/panel.test.tsx` covers the drawer and
delivery rows, and `tests/test_permissions.py` covers the ask rule. They
fail before the change, because `project inbox unclear` and the `unclear`
field don't exist, `revise` refuses a notice, and `ask` and `request`
accept a fifth option.

## 5. Cost

The panel's tick reads the store only when a file changes, and
`needsDelivery` checks one more field per item in memory, so an idle tick
does no new work. A press runs one CLI process, as dismiss does, and its
delivery runs `claim` and `delivered`, as every delivered line does. The
descriptions of `ask`, `tell`, `request`, and `revise` each grow by a
sentence in every Claude Code session's tool list.

## 6. Rollout

The change ships as one pull request. Its release notes tell people to run
`project init` again, which writes the new ask rule into their settings and
the new agents block into their instructions. Until they do,
`Bash(project *)` lets an agent run `project inbox unclear` without asking.

## 7. Open questions

- **The look.** The label's wording, the `unclear` mark's `warning` color,
  and closing the drawer on the press are a first choice. Owner: ninjudd,
  who adjusts the panel in daily use, as the [inbox](../readme.md) plan's
  § 7 records for questions and notices.

## 8. What the implementation settled

Building § 2 settled these details. Each one about the look is a first
choice under § 7.

- **`started` and `unclear` are one kind of line.** `OPEN_LINES` in
  `src/projector/inbox.py` names the lines an open item owes without being
  resolved. `claim`, `settle`, and `clear_for_outcome` treat both alike, and
  `settle` marks the field of the line's own name delivered, or removes it
  when the delivery failed.
- **An answer keeps a claimed `UNCLEAR`.** `answer` clears what it replaces
  through `clear_for_outcome`, as `done` and the dismissals do, so a claimed
  `UNCLEAR` on a question reaches its agent before the answer.
- **A restore takes back `unclear`.** The dismissal settled the person's
  word, as it settles a request's start, so a restored item waits for a new
  press.
- **A stored item over the cap must shrink to be revised.** `revise` checks
  a question's options on every revision, so an agent that revises a
  question stored with five other options must give four or fewer. A
  request's options are checked only when `revise` replaces them.
- **The mark sits before a request's stage.** An open request that is both
  unclear and started shows `unclear`, then `starting`, `ready`, or
  `blocked`, because the stage still says whether you can begin.
- **Enter on a notice's line puts the ring on discuss.** The ring skips
  **I don't understand** above it, so pressing Enter twice fills the prompt
  and never sends `UNCLEAR` by accident.
- **Every item can carry a claim.** `hooks/types.d.ts` declares `claim` on
  `Posted`, with `unclear` among its lines, since a notice can now be
  claimed. The `restore` tool's result for a notice names `UNCLEAR` too.

## 9. Outcome

The project shipped in one pull request, as § 6 planned, with the design
of § 2 and the details § 8 records. The look stays the owner's to adjust,
as § 7 says. In the table, `UnclearTests` and the other Python classes are in
`tests/test_inbox.py`.

| Criterion in § 4 | Evidence |
| --- | --- |
| Each open kind's drawer offers `6`; a resolved one does not | `panel.test.tsx`: `a drawer’s actions follow its options a line each, on 6 to 9, named for what they act on` |
| A running subagent's notice: `unclear`, one `UNCLEAR`, the mark, no action | `panel.test.tsx`: `I don’t understand on a running subagent’s notice records it, sends UNCLEAR once, and marks the line until a revise` |
| A finished subagent's item reaches the main loop with the note | `panel.test.tsx`: `I don’t understand on an item whose subagent finished reaches the main loop, naming the subagent, and marks it beside its stage`; `inbox.test.ts`: `the main loop is told which subagent posted an item it receives UNCLEAR for` |
| `revise --notice` keeps the number, drops the mark, and offers `6` again | `ReviseTests.test_revises_a_notice_with_its_own_flags_and_keeps_its_number`, `UnclearTests.test_watch_sends_unclear_once_and_the_mark_stands_until_a_revision`, and the panel test of the running subagent's notice |
| A failed delivery takes the mark back with a toast | `UnclearTests.test_a_failed_unclear_removes_it`; `panel.test.tsx`: `an UNCLEAR that cannot be delivered takes the mark back, says so, and offers I don’t understand again` |
| A second press, a resolved item, and a stale drawer record nothing | `UnclearTests.test_refuses_text_the_agent_revised_a_second_press_and_a_resolved_item`; `panel.test.tsx`: `a press of I don’t understand on a drawer the agent revised since records nothing and shows the new text` |
| An answer or dismissal replaces an unclaimed `UNCLEAR`, and follows a claimed one | `UnclearTests.test_an_answer_or_dismissal_replaces_an_unclear_no_deliverer_claimed`, `test_an_answer_or_outcome_recorded_while_unclear_is_claimed_follows_it`, `test_a_dismissal_while_a_notices_unclear_is_claimed_lets_that_line_settle` |
| Five options exit `65` for `ask`, `request`, and `revise` | `AskTests.test_refuses_a_question_that_fails_a_check_and_writes_nothing`, `ReviseTests.test_refuses_a_revision_that_fails_a_check_and_changes_nothing`, `RequestTests.test_records_the_options_the_agent_gives_and_refuses_ones_that_fail_a_check`, `test_revise_replaces_or_drops_a_requests_options`; `inbox.test.ts`: `ask, request, and revise take at most four options, leaving 6 for I don’t understand` |
| A stored six-choice question, or a request with Start and five options, keeps its digits and offers the action without one | `panel.test.tsx`: `a question stored with six choices, or a request with Start and five options, keeps them on 1 to 6 and offers I don’t understand without a digit` |
| An agent's `project inbox unclear` asks | The `unclear` row of `DECISIONS` in `tests/test_permissions.py` |
| `watch --once` prints `UNCLEAR` and marks it delivered | `UnclearTests.test_watch_sends_unclear_once_and_the_mark_stands_until_a_revision` |

As § 5 planned, the panel's idle tick does no new work. `needsDelivery`
reads one more field in memory, and a press runs one CLI process, as a
dismissal does. The `started` handling in `claim`, `settle`, and
`clear_for_outcome` now covers `unclear` through `OPEN_LINES`, so the store
holds no second copy of it. `hooks/typecheck.sh` needs the declarations
that an interactive `claude --plugin-dir .` session writes. No checkout this
change was built in had them, so the hooks were type-checked instead against
the declarations Claude Code 2.1.277 published. The head and its base gave
the same 75 errors, all from those older declarations, so the change adds
none they can see.
