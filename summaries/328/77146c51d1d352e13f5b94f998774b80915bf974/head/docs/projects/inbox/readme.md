---
status: completed
priority: now
---

# An inbox for an agent's questions and notices

An agent can put two kinds of item in your inbox, a question for you to
answer or a notice for you to read, and `project inbox` and Projector's band
show them, let you snooze them, and clear them.

## 1. Problem

`project question` ([questions](../questions/readme.md)) lets an agent ask
you a question without stopping. The question waits in a store kept for its
session, appears in Projector's band above the Claude Code prompt, and your
answer goes back to the agent that asked. An agent also needs to tell you
things that need no answer, only for you to read them, and has nowhere to
put them but the transcript:

- **The answer to a question you asked it.** In a loop's session you ask
  something, step away, and the reply scrolls away under watcher
  notifications and subagent reports.
- **Something it did that you should know about.** A force-push, a pull
  request it closed, a finding it declined: today each is a line in the
  transcript, and nothing says whether you saw it.

A question bent to carry these, with a recommendation of "OK" and no other
options, puts a choice in front of you that is not one, and sends its answer
to an agent that is not waiting for it. A second store beside the questions
would duplicate the CLI, the band's list, and the drawer, and give you two
places to look. And an item you cannot deal with yet has only two fates
today: it stays in front of you, or you dismiss it and lose it.

## 2. Solution

Rename `project question` to `project inbox`, and give each item a kind:
`question`, as today, or `notice`. An agent posts a notice with `project
inbox tell`, or in Claude Code with a new `tell` tool beside `ask`. Like a
question, a notice is one line that expands in the band to show its detail,
and may name a pull request; unlike a question, it has no options. It
shares the session's store, numbering, and references with the questions
and appears in the same list in the band. Every item's drawer ends with
`discuss | dismiss | snooze`. Dismissing a notice clears it and
marks it delivered at once, because nothing goes back to the agent;
dismissing a question still tells the agent `DISMISSED`. Snoozing either
kind hides it for an hour, after which it returns as if just posted.
Questions keep everything else they do now under the new names. Release
0.7.14 shipped `project question`, but nobody uses it yet, so the rename
moves the store to `projector/inbox/` and drops the old names without
migrating anything.

### 2.1 The flow

1. An agent posts an item: `project inbox ask` for a question, `project
   inbox tell` for a notice, or the `ask` and `tell` tools in Claude Code.
   Both kinds take the next number in the same counts, so a notice about
   #337 after its third question is `#337.4`, and one about no pull request
   is the next `q` number.
2. The band reads the store on its tick as it does now, and lists the
   items that are neither resolved nor snoozed, questions and notices
   together, below the pull requests. An item it has not shown before brings
   the band back, and an item posted through a tool raises one
   `$.ui.notify`.
3. You open a notice's drawer: its detail, then `discuss | dismiss |
   snooze`. **dismiss** runs `project inbox dismiss`, which, for a
   notice, records the dismissal and sets `deliveredAt` to the same moment.
   The line stays, in the `subtle` color, for 30 seconds and then leaves
   the band, as an answered question does (§ 10). A question's drawer
   works as it does now, with **snooze** added after **dismiss** and with
   no `Type something` field (§ 10).
4. **snooze** runs `project inbox snooze`, which records `snoozedUntil`, an
   hour away. The item leaves the band and its divider's counts at once.
   When that time passes, the band shows it again and brings back a hidden
   band, as it does for a new item. From a terminal, `project inbox snooze
   <reference> --for <minutes>` picks the time, and `--for 0` wakes the item
   now.
5. Everything that delivers handles questions only: `claim`, `delivered`,
   `reopen`, `watch`, the band's deliverer, and the band's reading of
   answers out of a sent prompt. A notice is never claimed, and a quoted
   notice in a prompt answers nothing. Snoozing changes no delivery: a
   snoozed item can still be answered or dismissed from a terminal. Either
   one clears `snoozedUntil`, so a resolved item never returns.
6. `project inbox context` explains a notice's reference as it explains a
   question's, so an agent reading `#337.4` in a prompt learns which notice
   you mean.

### 2.2 The CLI

Every subcommand keeps its arguments, `--session`, `PROJECTOR_SESSION`,
`--json`, and exit codes, under `project inbox`. `project question` is
removed, so `project question ...` exits `2` as any unknown command does.

| Command | Change |
| --- | --- |
| `project inbox ask <question> --recommend …` | Renamed from `question ask`; records `kind: question` |
| `project inbox tell <notice> [--detail <markdown> \| --detail-file <path>] [--pr <owner/repo#number>] [--agent <id>]` | New: checks the line, numbers it in the shared counts, records `kind: notice`, and prints its reference |
| `project inbox list [--all]` | Renamed; lists both kinds, snoozed ones included. Text output is `ref`, `kind`, state, and subject, tab-separated, after the session when listing every session. An item whose `snoozedUntil` is still ahead is `snoozed until <HH:MM>`, in local time; a notice's other states are `open` and `dismissed` |
| `project inbox show <reference>` | Renamed; a notice prints its reference and subject, `about <pr>`, its detail, and its state, with no options |
| `project inbox answer` | Renamed; exits `65` for a notice, naming `dismiss`; answering clears `snoozedUntil` |
| `project inbox dismiss <reference>` | Renamed, and takes either kind. A notice records `{ dismissed: true, answeredAt }` and `deliveredAt` together; a question, as now, waits for its deliverer. Dismissing either kind clears `snoozedUntil` |
| `project inbox snooze <reference> [--for <minutes>]` | New: sets `snoozedUntil` to `--for` minutes from now, 60 by default; `--for 0` sets it to now, which wakes the item. Exits `65` for an item already answered or dismissed, and for a negative `--for` |
| `project inbox claim`, `delivered`, `reopen` | Renamed; `claim` exits `65` for a notice, which has nothing to deliver |
| `project inbox watch` | Renamed; unchanged, since a dismissed notice already has `deliveredAt` |
| `project inbox context` | Renamed; a notice's reference prints `<ref> is the notice: <subject>`. With a letter, such as `#12.2.a`, it prints nothing, because a notice has no answers to name |

`tell` refuses, with exit `65`, a notice of more than one line or longer
than 120 characters, an empty notice, and a `--pr` that is not
`owner/repo#number`, by the same checks `ask` makes of its question. With
`--json`, every command that prints one item prints `{ "item": … }` where it
printed `{ "question": … }`, and `list` prints `{ "items": […] }`.

### 2.3 The store

A session's items live in `$XDG_STATE_HOME/projector/inbox/<session>/`, or
under `~/.local/state/projector/inbox/` when `XDG_STATE_HOME` is unset, with
the same `counts.json`, `session.json`, operating-system lock, and file per
item that `projector/questions/` holds today. The CLI never reads
`projector/questions/`, and leaves any it finds in place. A session keeps
its 100 most recently delivered items, either kind.

| Field | Value |
| --- | --- |
| `kind` | New: `question` or `notice` |
| `ref`, `number`, `pr`, `agentId` | Unchanged; `agentId` is the posting subagent's id |
| `postedAt` | Renamed from `askedAt`: when the agent posted it, in milliseconds |
| `subject` | Renamed from `question`: the question or the notice, in one line of at most 120 characters |
| `detail` | Unchanged |
| `choices` | A question's answers in letter order, as now; absent on a notice |
| `answer` | A question's as now; a notice's `{ dismissed: true, answeredAt }` once dismissed |
| `snoozedUntil` | New: when a snoozed item returns, in milliseconds; absent until the item is first snoozed, and cleared when the item is answered or dismissed |
| `claim` | A question's only |
| `deliveredAt` | As now; a notice's is set when it is dismissed |

### 2.4 Claude Code: the tools

The hooks module registers `tell` beside `ask` in `session.start`, with
`isDeferred: false`, so the model sees `mcp__projector__tell` from the
first turn. It takes `notice`, `detail`, and `pr`, and its description
tells the agent to post one short line, put the rest in `detail`, name the
pull request in `pr`, and use it for the answer to a question you asked or
for something it did that you should know about, and that nothing comes
back to it. Its `tool.call` hook runs `project inbox tell` with `--session`
from `$.session.id()` and `--agent` from the call's `agentId`, and answers
`Posted <ref>. It waits in Projector's band until the person dismisses it;
nothing comes back to you.` A refusal follows `ask`'s rules: exit `65` gives
the check that failed, another exit gives the error and `run project
upgrade`, and a `project` that does not finish gives `ask in the
conversation instead`. `ask` is unchanged but for running `project inbox
ask`.

### 2.5 Claude Code: the band

The band draws questions as it does now and notices beside them, oldest
first. § 10 gives the reasons for what **discuss** puts in the prompt,
for a question's drawer without a `Type something` field, for how long a
resolved line stays, and for the age that ends a pull request's line, in
the column where the items' ages end theirs. In a terminal, with one
question and one notice about #337:

```text
──────────────────────────── 1 question · 1 notice · updated 12s ago [-]

#337 draft Retry failed webhook deliveries with backoff                 1h

- Decline the finding about the retry budget?                     #337  5m
• Force-pushed #337 to drop a stray debug file                    #337  1m
```

- **A notice's line.** The same line as a question's, with the bullet `•`
  in place of `-`, so a glance tells which items want an answer and which
  want a read. The bullet and the subject are one plain `Button` that opens
  the drawer.
- **The divider** counts open questions and open notices separately,
  `1 question · 2 notices`, leaving out a count of zero and any snoozed
  item. Only a terminal draws the divider (§ 9), so only there does the
  band count its items.
- **A notice's drawer.** A blank line, its detail as `Markdown` and another
  blank line when it has detail, then `discuss | dismiss | snooze`: three
  plain `Button`s, each drawn as its label alone, with a `|` in the
  `subtle` color between them, in place of the bracketed buttons the
  drawer draws today. **discuss** puts the quoted subject and two newlines
  in the prompt, as it does for a question, and what you type under a
  quoted notice answers nothing. **dismiss** runs `project inbox dismiss`,
  and **snooze** runs `project inbox snooze`; each puts the item the
  command prints in the band's list, and a second press finds the item
  changed and does nothing.
- **A question's drawer** lists its options and ends the same way,
  `discuss | dismiss | snooze`, gaining **snooze** after **dismiss**. It
  has no `Type something` field: what you type under the quote that
  **discuss** puts in the prompt is your answer. Sending a prompt that
  answers the question closes its drawer.
- **A snoozed item** leaves the list and closes its drawer. On each tick,
  shown or hidden, the band checks for an open item whose `snoozedUntil`
  has passed since its last tick; such an item returns to the list, and the
  band appears if you hid it. Waking needs no `list`, because the band
  already holds the item.
- **A dismissed notice's line.** In the `subtle` color and struck through
  for 30 seconds after `deliveredAt`, then gone. Its drawer still opens,
  with only **discuss**.
- **Sent prompts.** The reader of answers in a sent prompt considers
  questions only, snoozed ones included.

### 2.6 Files

| File | Layer | Change |
| --- | --- | --- |
| `src/projector/questions.py` → `src/projector/inbox.py` | 1 | `git mv`; the store root `projector/inbox`; `QuestionError`, `QuestionNotFound`, `AmbiguousQuestion` → `InboxError`, `ItemNotFound`, `AmbiguousItem`; `question_file`, `load_questions` → `item_file`, `load_items`; `kind`, `postedAt`, and `subject` in every record it writes and reads |
| `src/projector/inbox.py` | 2 | `tell`, `snooze`, a notice's `dismiss`, the kind check in `answer` and `claim`, the notice line in `context` and its silence on a notice's reference with a letter, and `answer` and `dismiss` clearing `snoozedUntil`; `check` splits into the subject and pull request checks both kinds share, which `tell` reuses, and the recommendation and option checks only `ask` makes |
| `src/projector/cli.py` | 1, 2 | The `question` command becomes `inbox`, with `{ "item" }` and `{ "items" }` in its JSON; layer 2 adds `tell`, `snooze`, the kind column, the snoozed state, and a notice's `show` |
| `tests/test_questions.py` → `tests/test_inbox.py` | 1, 2 | `git mv` and rename in layer 1, every existing test passing under the new names; layer 2 adds the notice and snooze tests in § 4 |
| `docs/cli.md` | 1, 2 | "Ask and answer questions" becomes "Use the inbox", with `project inbox` throughout; layer 2 adds `tell` and `snooze` and a notice's `dismiss` |
| `hooks/questions.ts` → `hooks/inbox.ts`, `hooks/questions.test.ts` → `hooks/inbox.test.ts` | 1, 2 | `git mv`, with `project inbox` and the renamed fields; layer 2 adds the `tell` tool's description, schema, and arguments, which items are shown at a time, and which items woke since the last tick, and leaves notices out of the answers a sent prompt gives |
| `hooks/types.d.ts` | 1, 2 | `Question` becomes `Item`, with `kind`, `postedAt`, `subject`, and in layer 2 `snoozedUntil`; the state key `bandQuestions` becomes `bandInbox` |
| `hooks/register.tsx` | 1, 2 | Run `project inbox`; layer 2 registers and serves `tell`, and draws notices, **snooze** in both drawers, the divider's counts, and the waking of a snoozed item |
| `hooks/band.test.tsx` | 1, 2 | Every existing test passing under the new names; layer 2 adds the notice and snooze tests in § 4 |
| `docs/plugins.md` | 1, 2 | "Answer your agents' questions in Projector's band" becomes "Read your inbox in Projector's band"; layer 2 describes notices and snoozing |
| `docs/projects/questions/readme.md` | 1 | Status `completed`, with a final section saying it shipped in 0.7.14 and continues as `inbox`, whose § 7 carries each of its open questions |
| `docs/projects/inbox/readme.md` | 1, 2 | Status `in-progress` in layer 1; what layer 2 settles |

No skill changes. A loop skill that posts through `project inbox` needs the
session's id in every host, which § 7 leaves open.

## 3. Why this design

- **One inbox, two kinds.** A question and a notice live the same life: an
  agent posts it, it waits for you in one place, and one action of yours
  clears it. Keeping both in one store reuses the numbering, the lock, the
  band's reading of the store, its list, its drawer, and its 30 seconds of
  fading, and gives you one list to read. A third kind of store would
  duplicate all of it.
- **A notice is not a question with one option.** A choice you cannot
  refuse is not a choice, and an answer the agent is not waiting for is
  noise in its transcript. A notice has no options and sends nothing back.
- **One set of actions.** Every drawer ends `discuss | dismiss | snooze`,
  so the same place does the same thing whatever the kind.
  Dismissing is how you clear a notice you have read, and how you set
  aside a question you will not answer; the kind decides only whether the
  agent hears of it.
- **Dismissing a notice marks it delivered.** With `deliveredAt` set at
  once, retention, `watch`, the band's deliverer, and the band's 30
  seconds of fading all handle a dismissed notice with no new branch: it
  is simply a delivered item.
- **Snoozing hides without losing.** An item you cannot deal with yet
  should leave your way without leaving the inbox. Snoozing records a time
  rather than a state, so nothing has to run when it ends: the band and
  `list` compare the time with the clock. An hour is long enough to clear a
  view and short enough that nothing is forgotten; the CLI takes any other
  time. Answering or dismissing an item clears the time, because a
  resolved item has nothing to return for.
- **Nothing goes back to the agent for a notice.** A notice reports what is
  already done or answers what you already asked, so the agent keeps
  working whether or not you have read it. An agent that needs you to see
  something before it goes on asks a question instead.
- **Keyed by session, not by branch.** Each session keeps its own items,
  and an answer goes back to the session that asked. Keying the inbox by
  branch, so that every session on one branch shared its items, was
  considered and rejected for three reasons. Only the asking session has
  the context: the agent that waits for the answer, and the transcript
  that explains the question, are in that session. A loop spans branches:
  one watch loop's session tracks many pull requests, each on its own
  branch, so keying by branch would split one session's items. And a
  worktree can have a detached head, with no branch to key by.
- **Rename now, with no migration.** The names `question` and `questions`
  no longer describe what the command keeps. Nobody depends on them yet, so
  renaming the command, the module, the store's directory, the JSON keys,
  and the record's fields costs one pull request today, where it would cost
  a migration later.
- **Two layers.** The rename touches every file the questions work added
  and changes no behavior, so a reviewer checks it by seeing every existing
  test pass under the new names. Notices and snoozing are new behavior.
  Reviewing the two apart keeps each argument whole.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| Run every test `tests/test_questions.py` held, renamed into `tests/test_inbox.py` | Each passes against `project inbox` |
| `project question list` | Exits `2` as an unknown command |
| `project inbox ask` in a scratch `XDG_STATE_HOME` | The item lands in `projector/inbox/<session>/` with `kind: question`, `postedAt`, and `subject`; `projector/questions/` is not created |
| `project inbox tell "Force-pushed #12 to drop a stray file" --pr o/r#12 --session s1`, after one question about #12, from a checkout of `o/r` | Prints `#12.2`; the item has `kind: notice` and no `choices` |
| `tell` with two lines, 121 characters, an empty line, or `--pr 12` | Exits `65` with the reason and writes nothing |
| `project inbox dismiss '#12.2' --session s1` | Records `{ dismissed: true, answeredAt }` and `deliveredAt`; a second `dismiss` exits `65` |
| `project inbox dismiss` on a question | Records the dismissal without `deliveredAt`, as now; `watch` then prints its `DISMISSED` line |
| `answer` or `claim` on a notice | Exits `65` naming `dismiss` |
| `project inbox snooze '#12.1' --session s1`, then `list` | `list` shows `#12.1` as `snoozed until <HH:MM>`, an hour ahead; `snooze --for 0` shows it `open` again |
| `snooze` on a dismissed item, or with `--for -1` | Exits `65` |
| `answer` on a snoozed question | Records the answer and clears `snoozedUntil` |
| `project inbox dismiss` on a snoozed notice, and on a snoozed question | Each records the dismissal and clears `snoozedUntil`; `list` shows the notice `dismissed` and neither as `snoozed until` |
| `project inbox watch --once` with only a dismissed notice | Prints nothing |
| `printf 'see #12.2' \| project inbox context --session s1` | Prints `#12.2 is the notice: Force-pushed #12 to drop a stray file` |
| `printf 'see #12.2.a' \| project inbox context --session s1` | Prints nothing and exits `0` |
| A subagent calls `mcp__projector__tell` about #7 | Returns `Posted #7.1. …`; the band lists a `•` line naming `#7`, the divider counts `1 notice`, and one `$.ui.notify` is raised |
| Open a notice's drawer | Its detail, then `discuss \| dismiss \| snooze` as three plain buttons |
| Press **dismiss** in a notice's drawer | Runs `project inbox dismiss`; the line turns to the `subtle` color and is struck through; no `$.session.send` or `$.prompt.submit` follows; the line leaves the band 30 seconds later |
| Press **snooze** in either kind's drawer | Runs `project inbox snooze`; the item leaves the list and the divider's count; with the band hidden, an hour later the item returns and the band appears |
| Snooze an item, hide the band, and dismiss the item from a terminal | An hour later the item does not return and the band stays hidden |
| Press **discuss** in a notice's drawer | The prompt gains the quoted subject and two newlines |
| Send a prompt quoting a notice's subject and a line under it | Nothing is recorded |
| With no items | The band, its divider, and the tick look and act as they do now |
| On the main screen under tmux, ctrl+x tab and Enter on an item | Its drawer opens; a question's option numbers, `Markdown` links, take no press there |

`tests/test_inbox.py` covers the CLI rows, and `hooks/inbox.test.ts` and
`hooks/band.test.tsx` cover the band rows but the last, answering
`process.run` from memory and moving the clock as the band tests do now.
Before layer 2, `tell`, `snooze`, a notice's `dismiss`, the kind checks,
the notice's `context` line, the `tell` tool, the notice's line and drawer,
and the waking of a snoozed item each fail their test. Check the
main-screen row by hand under tmux.

## 5. Cost

Nothing here adds work to a path that runs often. `tell`, `dismiss`, and
`snooze` are one process each, run when an agent posts or you act on an
item. A notice is never claimed, renewed, or delivered. Waking a snoozed
item compares each held item's `snoozedUntil` with the clock on the tick
the band already runs, and starts no process. The `tell` tool's schema
rides in every request beside `ask`'s: a name, a paragraph, and three
properties.

## 6. Rollout

Build it as a GitHub stack of two pull requests, as `implement` describes
under "Stack dependent work":

1. **Rename `project question` to `project inbox`.** Layer 1 of § 2.6, with
   no new behavior. It moves the `questions` project to `completed` and this
   one to `in-progress`.
2. **Add notices and snoozing.** Layer 2 of § 2.6, on top of the first.

This project moves to `completed` once the main-screen row in § 4 has been
checked by hand and the changes in § 10 and § 11 have shipped; until then
it stays `in-progress`.

## 7. Open questions

The first four carry the questions plan's open questions under the inbox's
names, so that marking that plan `completed` in layer 1 leaves none of
them untracked.

- **A session's id outside Claude Code.** A Codex agent or a Paseo runner
  needs to pass its own id to `--session` or `PROJECTOR_SESSION`, and which
  id each host offers is not settled. Until it is, an agent there passes an
  id its runner chooses. Owner: ninjudd, with Jason Fieldman for Paseo.
- **The loop skills posting through the inbox.** Once every host can name
  its session, `fix-pr` and the loops can ask and tell through `project
  inbox` instead of handing everything to the main loop. Deferred until the
  question above is settled.
- **Views for Codex and Paseo.** Codex has no band, and Paseo's clients
  could list and clear items through `project inbox list --json`,
  `answer`, and `dismiss`. Deferred until a session's id outside Claude
  Code is settled, the first question here: a view shows one session's
  items, so it must name its session the way that session's agents do, or
  it shows another session's items or none.
- **Old session directories.** A session's directory under
  `projector/inbox/` stays after the session ends. Removing directories
  older than some age is deferred until they turn out to pile up.
- **The look of notices and snoozing.** The `•` bullet, the divider's
  wording, the hour a snooze lasts, and whether the band says how many
  items are snoozed are a first choice. Owner: ninjudd, who has been
  adjusting the band in daily use since layer 2 shipped in 0.7.15. § 9 and
  § 10 record the adjustments so far. Whatever stands when the main-screen
  check of § 4 closes the project is the design.
- **Old `projector/questions/` directories.** Left on disk, unread.
  Removing them is deferred, since no one but the owner has used the
  command.
- **Revising an open item in place.** After you discuss a question, an
  agent that should offer something else can only ask a new question, and
  the old one stays open beside it until you dismiss it. A revision would
  keep the item and its number instead. `project inbox ask --revise
  <number>` and `project inbox tell --revise <number>`, and a `revise`
  property on both tools, would replace the item's `subject`, `choices`,
  and `detail` after the checks `ask` or `tell` makes, keep its
  `postedAt`, `agentId`, and `snoozedUntil`, and record `revisedAt`. The
  band would redraw the item where it was, forget any answer its drawer
  had put in the prompt for it, and raise no notification. Under § 11 a
  number holds no pull request, so a revision could change `pr` as well.
  Deferred by the owner: asking again and dismissing the old question
  already works, so the flag, the tool property, and the band's redraw
  wait until revising proves common.
- **Answering only under the band's own proposals.** Considered and set
  aside. Under this rule, a quoted question in a sent prompt would answer
  only while the band held a proposal for it, the text an option put in
  the prompt, so that a message typed under a quote that **discuss** put
  there was never recorded as an answer. § 10 makes **discuss** the way to
  answer in your own words: what you type under the quoted question is
  your answer. A quoted question followed by text is therefore an answer
  on purpose, and this rule would undo that. To talk a question over
  without answering it, write on the quote's own line. Owner: ninjudd, to
  reopen if a message meant as discussion is recorded as an answer.
- **Finding items across sessions.** A view or a shell user could want
  every session's open items about one pull request, as `project inbox
  list --pr owner/repo#337` would list them, each with its session.
  Answers would still go to the session that asked, because only that
  session's band or `watch` delivers them, so you would answer with that
  session's `--session`. Deferred until a view needs it: `project inbox
  list --json` without `--session` already prints every session's items
  with their `pr`, which a script can filter.

## 8. Background

The questions project shipped in two pull requests, #271 for the CLI and
#273 for the band and the `ask` tool, released in 0.7.14. Using the band
showed the gap this plan fills: the agent had answers and reports for the
owner as well as questions, and only questions had a place to wait. A
notice as a third, separate system was considered and rejected in favor of
one store with two kinds, for the reasons in § 3. The owner chose the name
`inbox`, the actions `discuss | dismiss | snooze` for both kinds,
and snoozing for both.

## 9. What the implementation settled

Both layers of § 6 are built, and shipped in 0.7.15 with #281, #283, and
#284, which changed the band around them. Together they settled these
points, which the sections above leave open or state loosely:

- **A dismissed notice lists only with `--all`.** Dismissing a notice marks
  it delivered, and `list` without `--all` leaves out delivered items. The
  § 4 row that dismisses a snoozed notice therefore reads `list --all` to see
  it as `dismissed`.
- **`snooze` takes whole minutes and prints the new state.** `--for` is an
  integer. `snooze` prints the reference and the item's state, such as
  `#12.1 snoozed until 14:32`, or `#12.1 open` after `--for 0`.
- **`list` skips a file of no known kind.** A store file whose `kind` is
  neither `question` nor `notice` is skipped, as a file whose name does not
  match its reference already is.
- **The `tell` tool sends the agent to the conversation in its own words.**
  When `project` does not finish, the refusal ends `tell the person in the
  conversation instead`, because the agent has nothing to ask. Every other
  refusal follows `ask`'s rules, as § 2.4 says.
- **The hooks type each kind.** `Item` in `hooks/types.d.ts` is `Question`
  or `Notice`, so the deliverer and the reader of answers in a sent prompt
  accept only a question.
- **Waking counts from the band's previous tick.** The first tick after the
  hooks module loads wakes nothing. An item whose snooze ended while no copy
  of the module ran returns to the list, but does not bring back a hidden
  band. A tick reads the clock before the store, so a snooze that ends
  while the store is read wakes on the next tick.
- **`--for 0` ends a snooze now rather than removing it.** It sets
  `snoozedUntil` to the current time, so the band's next tick sees the item
  wake and brings back a band you hid, as it does when a snooze runs out.
- **A blank line sets the divider apart.** #283 draws a blank line under
  the divider, both when the band lists pull requests or items and when it
  has nothing to list, so the divider's counts and its `updated …` time do
  not run into the first row.
- **Only a terminal draws the divider.** #284 draws the divider and the
  blank line under it only when the surface is `terminal`. Claude Desktop,
  VS Code, and the mobile app set the band's text in their own font rather
  than in a terminal's columns, so a rule sized to those columns would
  wrap. There the band starts with its first row, and shows neither the
  item counts nor the `updated …` time.
- **A notice's drawer drops the divider too.** #281 made an open
  question's drawer the band's top row, with no divider above it. In a
  terminal that gives the drawer two more rows before the engine, which
  caps the band below half the terminal, starts to scroll it: the
  divider's row and the blank row under it. On any other surface the band
  draws no divider, so the drawer gains no row. Every item's
  drawer, a notice's included, has the band to itself the same way. The
  divider and its counts return when the drawer closes or the item leaves
  the list, as a snooze makes it do.
- **A snooze lasts a day at most.** `snooze` refuses a `--for` over 1440
  minutes before it writes anything, and a state whose wake time is not
  today names the day, as `snoozed until tomorrow 01:30`.
- **A dismissed question lists as `dismissed`,** as a dismissed notice
  does, before and after its dismissal is delivered.
- **A command that waits out the store's lock exits 75,** not 65, so the
  band shows the failure instead of reading it as an item resolved elsewhere.

The main-screen row of § 4 waited for a check by hand under tmux, so the
project stayed `in-progress` until the check that § 12 records.

## 10. The band after layer 2

After using layer 2, the owner decided six changes to the band, which
#295 builds, three to its pull request lines and drawers, which #297
builds, two more to its pull request lines and drawers, which #298
builds, and a **dismiss** action for a pull request, which #300 builds.
§ 2.1, § 2.5, and § 4 describe the band with these changes, and this
section gives their reasons, their files, and their evidence.
The `/projector <tracked file>` form that the questions plan describes
goes away.

- **Discuss puts the quote and a blank line in the prompt.** One handler
  serves both kinds: **discuss** puts the item's quoted subject and two
  newlines in the prompt, so the cursor waits a blank line below the
  quote. When you send the prompt, the first line that is not blank under
  a quoted question is your answer: the option whose text it is, or else
  a typed answer under the question's last letter. The band records it
  and marks it delivered, because the prompt carries it to the agent, as
  it does for an option you press. Under a quoted notice, what you type
  answers nothing and reaches the agent as conversation. To talk a
  question over without answering it, write on the quote's own line. You
  answer in your own words in the prompt, where you write everything else
  to the agent, with room for a long answer and for several answers in one
  message.
- **A question's drawer has no `Type something` field.** The drawer lists
  its options, then `discuss | dismiss | snooze`. **discuss** does what
  the field did, so two ways to type an answer differed only in where you
  typed. The field was also the one part of the drawer that the mobile app
  cannot draw, so every surface now draws the same drawer. Once a typed
  answer is recorded, the drawer still shows it after the options, marked
  `✓`.
- **Sending an answer closes the question's drawer.** When a sent prompt
  answers a question, from an option's text or from what you type under
  the quote, the band closes that question's drawer if it is the open
  one, so the band shows its list again. Pressing an option fills the
  prompt and leaves the drawer open until you send, and **discuss** leaves
  it open too, so the options and detail stay in view while you type. A
  sent reply that quotes a notice closes that notice's drawer the same way,
  though it answers nothing and the notice stays until it is dismissed.
- **A quote may end in spaces or tabs.** When the band reads a sent
  prompt, a quoted question followed by spaces or tabs and then a line
  break still ends its line, so the answer under it is read. You cannot
  see a trailing space in the prompt, so it must not decide whether your
  answer is recorded.
- **A resolved line stays 30 seconds.** An answered or dismissed item
  stays in the band for 30 seconds after `deliveredAt`, which
  `RESOLVED_MS` in `hooks/inbox.ts` sets, in place of a minute. That is
  long enough to see that your answer or dismissal took, and the list
  clears sooner. The line is drawn in the theme's `subtle` color, as all
  of the band's muted text is, because some terminals draw `dimColor` text
  at full strength.
- **`/projector` takes no argument.** The command shows or hides the band
  and does nothing more. The band lists only the loops whose watchers the
  session starts, which it learns when its `Bash` and `Monitor` hooks see
  the session run a watcher. Naming a tracked file by hand, such as
  another session's, goes away. The band is the session's own view, of
  its loops' pull requests and its inbox, so another session's loop
  belongs in that session's band, beside the agents that act on it. With
  no file named by hand, every loop the band lists takes its kind from the
  watcher the session started. When the band cannot tell which file a
  watcher's `--tracked` argument names, its toast says that the band does
  not list that loop. The band never scans the machine's processes to
  learn a loop's kind, and it leaves out a loop that the session's state
  holds without a kind, until the session starts a watcher on that file,
  whose entry then replaces it.
- **A pull request's line ends with its age.** Each pull request's line in
  the band ends with how long ago the pull request last changed, its
  `updatedAt`, in the `subtle` color and the short form an item's age
  takes, such as `7h`. Pull requests and items share one right-aligned age
  column as wide as the widest age in the band, so what comes before the
  ages ends in one column. The title fills its line, so a pull request's
  notes and age end it as an item's pull request label and age do. A pull
  request that GitHub gave no time for shows no age. The drawer's facts no
  longer end with "updated … ago"; the divider's "updated" time, when the
  band last loaded, stays. Open review threads show as a count after `⋮`
  in yellow, such as `⋮5`, the last note before the age, in place of the
  words "5 open threads".
- **A pull request's drawer starts under its state.** Its lines start one
  column past the number, under `draft` or `ready`, not where the titles
  start.
- **Each drawer link has a line of its own, and the actions come last.**
  **github** and **projector** each take a line, because a terminal
  without hyperlink support draws a link's URL after its text, and a line
  shared with another link cut that URL off. The row with **discuss**
  follows them, last in the drawer, so more actions can join it.
- **Pull requests are grouped by author.** The band lists your own pull
  requests first, then everyone else's, each author's together and the
  authors in order of login, with nothing between the groups; within a
  group the order is unchanged. Your own work and the reviews you owe
  other people no longer interleave. Another person's line names its
  author after `draft`, in the `subtle` color, in a column as wide as the
  longest of those logins, so their titles start in one column. Your own
  lines name no author, and the drawer's facts name none either. The band
  tells which pull requests are yours by the login of the account its
  `gh` is signed in as, which the query that asks for the pull requests
  also asks for, as `viewer { login }`, so a load still makes one `gh`
  call. The snapshot keeps the login beside the rows.
- **Dismiss keeps a pull request out of the session's loops.** A pull
  request's drawer has **dismiss** after **discuss**, on the actions' row
  with `|` between them, as an item's drawer draws its actions. It runs
  `project loop drop owner/repo#number --tracked <file>` once for each of
  the session's tracked files that lists the pull request, then closes the
  drawer, reloads the band so the line goes, and says which loops stopped,
  as `stopped watching and reviewing owner/repo#number`. When the CLI
  refuses, the band shows its error and keeps the line. `project loop drop`
  appends a `!owner/repo#number` line rather than rewriting the file,
  because a watcher may be appending a pull request it adopted at the same
  moment, and it changes nothing when the file already excludes the pull
  request in any letter case. `project loop restore` rewrites the file
  atomically without that pull request's `!` lines, so a dismissal can be
  undone. The CLI is the only writer, so the band and an agent edit a
  tracked file the same way. Both watchers honor a `!` line:
  `watch-prs.sh` already did, and `watch-threads.sh` now does instead of
  reporting it as a malformed line.

| File | Change |
| --- | --- |
| `hooks/register.tsx` | **discuss** puts the quoted subject and two newlines in the prompt; a question's drawer draws no `Input`, and a resolved question keeps the `✓` row of its typed answer; `/projector` drops its `argumentHint` and its handling of a tracked file, and its description names the inbox; `runningLoops`, the `ps -A` scan, goes, and `load` lists only the seen loops that have a kind; the toast for a tracked path the band cannot read says that the loop is not listed; muted text takes the `subtle` color; a sent prompt that answers a question closes its drawer, and a sent reply that quotes a notice closes the notice's drawer; a watcher's entry replaces a seen loop that has no kind |
| `hooks/parse.ts`, `hooks/parse.test.ts` | `expandPath`, which only `/projector <tracked file>` used, goes with its test |
| `hooks/types.d.ts` | `SeenLoop.kind` is required |
| `hooks/inbox.ts` | `answersIn` lets spaces or tabs follow a quote before its line break; `RESOLVED_MS` is `30_000` |
| `hooks/inbox.test.ts`, `hooks/band.test.tsx` | The rows below |
| `docs/plugins.md` | The drawer, **discuss**, which quotes answer, the 30 seconds, and the band without a file named by hand |
| `hooks/register.tsx` | Each pull request's line ends with its age, in a column shared with the items' ages through one helper, and its title grows to fill the line; the drawer drops "updated … ago" and `drawerIndent`, and puts **github** and **projector** on lines of their own; open review threads show as a yellow count after `⋮`, the last note before the age |
| `docs/plugins.md` | The band examples, the age at the end of a pull request's line, and the drawer under the state with one link per line |
| `hooks/github.ts`, `hooks/register.tsx`, `hooks/types.d.ts`, `docs/plugins.md` | The query asks for `viewer { login }`, and the snapshot keeps it; the band lists your pull requests first and then everyone else's by author, names another person's author before the title, drops "by" and the author from the drawer's facts, and draws the **discuss** row after the links |
| `src/projector/loop.py`, `src/projector/cli.py`, `tests/test_loop.py`, `skills/start-watch-loop/scripts/watch-threads.sh`, `skills/start-watch-loop/SKILL.md`, `tests/test_watchers.py`, `hooks/register.tsx`, `docs/cli.md`, `docs/plugins.md` | `project loop drop` and `restore` add and remove a tracked file's `!` lines; `watch-threads.sh` honors them; a pull request's drawer gains **dismiss**, which drops the pull request from each of the session's loops that lists it, through the CLI, on one actions' row shared with an item's drawer |

#295 also carries smaller fixes found while using the band, such as the
pull request drawer's **discuss** becoming a plain `Button`, and its
description lists them.

| Do | Expect |
| --- | --- |
| Open a question's drawer | Its detail, its numbered options, then `discuss \| dismiss \| snooze`, with no field |
| Press **discuss** on a question | The prompt gains the quoted subject and two newlines |
| Type `keep the cap, but log each retry` under that quote, and send | The band records it as a typed answer under the last letter and marks it delivered; the prompt reaches the agent as you wrote it |
| Type an option's text under the quote instead, and send | The band records that option |
| Type on the quote's own line, and send | Nothing is recorded |
| Press **discuss** on a notice, type under the quote, and send | Nothing is recorded; the prompt reaches the agent; the notice's drawer closes and the notice stays listed |
| Press an option's number, then send the prompt | The drawer stays open after the press and closes when the prompt is sent; the band shows its list |
| Press **discuss**, type an answer under the quote, and send | The drawer stays open while you type and closes when the prompt is sent |
| Send a quoted question, two spaces, a line break, and an option's text | The band records that option |
| Answer or dismiss an item | Its line stays, in the `subtle` color, for 30 seconds after `deliveredAt`, then leaves the band |
| Run `/projector` while another session runs a loop | The band shows or hides, and lists only the loops whose watchers this session started |
| Look at a pull request's line | It ends with how long ago the pull request changed, such as `7h`, in the `subtle` color, in the same column as the items' ages |
| Open a pull request's drawer | Its lines start under `draft` or `ready`; its facts name no author and end with the checks; **github** and **projector** each have a line, and the row with **discuss** comes last |
| Track your own pull requests and someone else's | Yours come first, naming no author; then the others, each author's together and the authors in order of login, each line naming its author in the `subtle` color before the title |
| Press **dismiss** in the drawer of a pull request both loops track | `project loop drop` runs once for each tracked file; the drawer closes, the line leaves the band, and a toast says `stopped watching and reviewing owner/repo#number` |
| Press **dismiss** when `project loop drop` refuses | The band shows the CLI's error, keeps the line, and leaves the drawer open |
| Run `project loop restore owner/repo#number --tracked <file>` | The `!` line goes, and the pull request returns to the loop and to the band on its next reload |

Before these changes, the rows for the drawer, for **discuss** on a
question and the two answers typed under its quote, for the two drawers
an answer closes, for a quote that ends in spaces, and for the 30 seconds
each fail their test. The drawer draws the field, **discuss** leaves a
space and no line break, a quote followed by a space answers nothing, an
answered question's drawer stays open, and a resolved line stays a
minute. Before #297, the rows for a pull request's line and its drawer
fail their test too: the line ends with no age, and the drawer starts
under the title, ends its facts with when the pull request was updated,
and puts its links on one line. Before #298, the rows for the drawer and
for grouping by author fail their test as well: the lines come in the
order the loops track them and name no author, and the drawer names the
author and puts **discuss** before the links. Before #300, the rows for
**dismiss** fail their test too, since the drawer has no **dismiss**, no
`project loop` command exists, and `watch-threads.sh` refuses a `!` line.

## 11. Number each session's items 1, 2, 3

Every item in a session takes the next number in one count, `1`, `2`,
`3`, across questions and notices, in place of a reference built from its
pull request. You never need to know an item's number: the band shows
none, and you answer by pressing in the band or by sending the quoted
question. The agent reads the number in what `ask` and `tell` return and
in the `ANSWER` and `DISMISSED` lines, and a shell user reads it in
`project inbox list`. `project inbox context`, which explains a reference
typed in a prompt, goes away with the references. This section replaces
the reference forms that § 2.1 and § 2.2 use and that the questions plan
defines in its § 2.3 and § 2.4.

### 11.1 Problem

An item's reference names its pull request so that you could type it in
a prompt: `#337.3` is the session's third item about #337,
`octo/web#12.1` the first about another repository's #12, and `q2` the
second about no pull request. Nothing asks you to type one. The band shows
no reference, and you answer a question by pressing in its drawer or by
sending its quoted subject. The forms still cost something:

- **Three forms and a session repository.** `post` in
  `src/projector/inbox.py` keeps a count for each pull request in
  `counts.json`, and records in `session.json` the repository of the first
  checkout that posts, so that only that repository's pull requests take
  the short `#337.3`. To find that repository, each `ask` and `tell` runs
  `git config` on the checkout's `origin`.
- **A reader of references in every prompt.** The band's `prompt.submit`
  hook tests each prompt you send with `mayReference`, and on a match runs
  `project inbox context`, which finds references with the `IN_TEXT`
  pattern and adds what they mean to the prompt's context. The `q` in `q2`
  exists only so that this reader skips the `2` in "rerun the 2 failing
  tests".
- **References where you read them.** The notification for a new item
  starts with its reference, as in `#337.3 Decline the finding about the
  retry budget?`, and the toast for an answer that did not reach its
  agent names the question by its reference alone.

### 11.2 The flow

1. **An agent posts an item.** `project inbox ask` or `tell`, run from
   the shell or by the `ask` and `tell` tools, takes the session's lock,
   reads `last` from `count.json`, writes `last + 1` there, and then
   writes the item to `<number>.json`. It prints the number, such as `3`.
   `--pr` still records what the item is about and must still be
   `owner/repo#number`, but it does not change the number.
2. **The tool answers with the number.** `ask` returns `Asked 3. Keep
   working on anything the answer does not block; it arrives as a message
   that starts ANSWER 3 or DISMISSED 3.`, and `tell` returns `Posted 3. It
   waits in Projector's band until the person dismisses it; nothing comes
   back to you.` Each raises a notification that holds the subject alone,
   under the title `Projector question` or `Projector notice`.
   `ASK_DESCRIPTION` ends: "The call returns the question's number, such
   as 3, and the answer arrives later as a message that starts `ANSWER
   3.<letter>` or `DISMISSED 3`."
3. **The band draws the item as it does now.** Its line shows the bullet,
   the subject, the pull request's label, and the age, and no number. The
   drawer, **discuss**, **dismiss**, **snooze**, and the answers a sent
   prompt gives work by the quoted subject or by the item the band holds,
   as they do now. When a delivery fails, the toast quotes the question's
   subject: `projector: the answer to "Decline the finding about the retry
   budget?" did not reach its agent (…); answer it again`.
4. **The answer reaches the agent with the number.** The band's deliverer
   or `watch` sends `ANSWER 3.a Decline the finding about the retry
   budget? → Decline it: the total cap already bounds retries to one
   host`, `ANSWER 3.c … → keep the cap, but log each retry` for a typed
   answer, or `DISMISSED 3 Decline the finding about the retry budget?`.
   The letters are unchanged: `a` is the recommendation, and the last
   letter is a typed answer.
5. **A shell user names an item by its number.** `project inbox list`
   prints the number first, then the kind, the state, and the subject,
   separated by tabs, with the session before them when it lists every
   session. `show 3`, `answer 3.a`, `answer 3.c --text …`, `dismiss 3`,
   and `snooze 3` name the item. Only `answer` takes a letter, and every
   other command refuses `3.a` with exit `65`. Without `--session` or
   `PROJECTOR_SESSION`, a command looks the number up in every session.
   `show`, `answer`, `dismiss`, and `snooze` look first among open items,
   those with no answer recorded, and among every item only when no
   session holds the number open (§ 11.10). Once two sessions hold a `3`
   that way, the command exits `67` and names both, and you pass
   `--session` with the session that `list` prints beside the item.
6. **Nothing reads a prompt for references.** The `prompt.submit` hook
   adds no context for anything you type, and `project inbox context` is
   gone. A `3` or a `#337.3.b` in a prompt reaches the agent as plain
   text.

### 11.3 The store

| Field or file | Value |
| --- | --- |
| `ref` | The item's number as a string, such as `"3"`: what the CLI prints and takes, and what the band keys an item by |
| `number` | The same number, as an integer |
| `pr` | Unchanged: the pull request the item is about, as `owner/repo#number`, or null. It no longer shapes `ref` |
| `<number>.json` | The item's file, such as `3.json` |
| `count.json` | New: `{ "last": 3 }`, the last number the session gave |
| `counts.json`, `session.json` | Neither read nor written |

Every other field keeps its meaning. An answer is still named by the
item's reference, a `.`, and the letter, as `3.a`, in `answer`'s argument
and in the `ANSWER` line.

### 11.4 Items stored under the old references

Nothing migrates. An item lives minutes or hours, and few people use the
inbox yet, so a migration would cost more than the items it would keep.
After the change:

- **The CLI refuses an old reference.** `parse_reference` refuses
  `#337.3`, `octo/web#12.1`, and `q2`, with or without a letter, with exit
  `65` and a message that gives the new form: `3`, or `3.a` for an answer.
- **Nothing reads an old item.** The check that decides whether a file
  holds an item, which `load_items` makes, also requires a `ref` that is a
  whole number, so it skips an old item as it skips a file of no known
  kind. `list`, `list --all`, `watch`, the band, and pruning never see an
  item posted before the change. Its file stays on disk, unread and never
  pruned, beside the session's `counts.json` and `session.json`, until the
  session's directory is removed (§ 7).
- **An agent waiting on an old question hears nothing more.** Answer or
  dismiss your open items before you upgrade, or ask the agent to ask
  again afterward.
- **An old session starts again at 1.** A session that posted before the
  change has no `count.json`, so its next item is `1`. `1.json` cannot
  overwrite an old item's file: an old file's name holds `%23`, the quoted
  `#`, or starts with `q`, and a new file's name is digits alone.

### 11.5 What goes away

- **`project inbox context`.** The subcommand in `src/projector/cli.py`,
  and `IN_TEXT`, `references_in`, and `context` in
  `src/projector/inbox.py`. `project inbox context` then exits `2`, as an
  unknown subcommand does.
- **The band's reading of references.** `mayReference` in
  `hooks/inbox.ts`, and `explain` and the context it adds in the
  `prompt.submit` hook in `hooks/register.tsx`. The hook still adds the
  note that names a subagent whose question your prompt answers.
- **The session's repository and the counts for each pull request.**
  `reference`, `session_repo`, and the `repo` argument of `post`, `ask`,
  and `tell` in `src/projector/inbox.py`, and `checkout_repo` in
  `src/projector/cli.py`, whose only callers are `ask` and `tell`. `PULL`
  stays, to check `--pr`.

### 11.6 Files

| File | Change |
| --- | --- |
| `src/projector/inbox.py` | `post` takes its number from `count.json`, still writing the count before the item, and loses `repo`; `REFERENCE` becomes `^(?P<ref>[1-9][0-9]*)(?:\.(?P<letter>[a-z]))?$`, and `parse_reference`'s refusal gives `3` and `3.a` as examples; the check that decides whether a file holds an item also requires a `ref` that is a whole number; the removals in § 11.5 |
| `src/projector/cli.py` | `ask` and `tell` pass no `repo`; the `context` subcommand and `checkout_repo` go; the help of `ask`, `tell`, `show`, and `answer` names a number, such as `3` and `3.a` |
| `tests/test_inbox.py` | The numbering tests expect `1`, `2`, `3` across pull requests, repositories, and kinds, and `1` again in another session; `test_shortens_only_the_repository_of_the_sessions_first_ask`, `ContextTests`, and `NoticeContextTests` go; every other test names items by number; the new rows in § 11.8 |
| `hooks/inbox.ts` | `ASK_DESCRIPTION` gives `3` as its example; `mayReference` goes |
| `hooks/register.tsx` | Both tools' notifications hold the subject alone; the toast for a failed delivery quotes the question's subject, from the band's list, in place of its reference; `explain` goes |
| `hooks/types.d.ts` | `Posted.ref`'s comment says it is the item's number, as a string; no type changes |
| `hooks/inbox.test.ts`, `hooks/band.test.tsx` | Fixtures numbered `1`, `2`; the fake CLI in `band.test.tsx` prints `String(posted)` and drops its `context` branch; `only text shaped like a reference starts the CLI` goes; `a reference typed in the prompt reaches the agent with its question` becomes the prompt row in § 11.8; the tool tests expect `Asked 1.`, `Posted 1.`, and a notification without a number |
| `docs/cli.md` | "Use the inbox" numbers its examples, drops the reference forms, the session's repository, and `project inbox context`, and says to pass `--session` from `list` when a number is in more than one session |
| `docs/plugins.md` | "Read your inbox in Projector's band" drops the sentence about a reference typed in a prompt, and says to find an item's number with `project inbox list` before you snooze it from a terminal |
| `docs/projects/inbox/readme.md` | What building this section settles |

### 11.7 Why this design

- **You never need a number.** The band answers by the quoted question
  and acts on the item it holds, so a number on the line would be one
  more thing to read and nothing to use. Only the agent and a shell user
  need a handle, and a short number is the easiest one to type, and to
  read in an `ANSWER` line.
- **One count, across kinds and pull requests.** A count for each pull
  request made the reference say which pull request the item is about,
  and the item's `pr` field and the band's label already say that. One
  count needs no session repository, no `git` call to find it, and no rule
  for which repository takes the short form.
- **A bare number is safe once nothing reads prompts.** `q2` and the `#`
  forms let a reader of prose tell a reference from a number in a
  sentence. With `project inbox context` gone, nothing reads a prompt for
  references, so a bare `3` cannot be mistaken for one.
- **The count has its own file.** Pruning deletes the oldest delivered
  items, which can include the one with the largest number, so a number
  taken from the files on disk could name a second item. A count that
  only grows names one item for the whole session. The file's new name,
  `count.json`, leaves an old session's `counts.json` unread rather than
  read with a new meaning.
- **`ref` stays, as a string.** The band keys an item, its drawer, and its
  deliveries by `ref`, and passes it back to the CLI as the CLI printed
  it. Keeping the field means a band and a CLI a release apart still work
  together: an older band sends `3.a` to a newer CLI, and a newer band
  passes an older CLI's `#337.3` back unchanged.

### 11.8 Acceptance criteria

| Do | Expect |
| --- | --- |
| In a checkout of `acme/app`, `project inbox ask "First?" --recommend x --pr acme/app#337 --session s1`, then `project inbox tell "Pushed" --pr octo/web#12 --session s1`, then `project inbox ask "No pull?" --recommend x --session s1` | Prints `1`, `2`, and `3`; the session's directory holds `1.json`, `2.json`, `3.json`, and `count.json` with `{ "last": 3 }`, and no `counts.json` or `session.json` |
| The same `ask` from a checkout of `octo/web` | Takes the next number in the same count |
| `project inbox ask "Other?" --recommend x --session s2` | Prints `1` |
| `project inbox list --session s1` | Prints a line for each item that starts with its number, `1`, `2`, or `3`, then its kind, `open`, and its subject |
| `project inbox answer 1.a --session s1`, then `project inbox watch --once --session s1` | `watch` prints `ANSWER 1.a First? → x` |
| `project inbox answer 1.a` with no session, when `s1` and `s2` each hold a `1` | Exits `67`, naming both sessions |
| `project inbox show '#337.1'`, `answer q1.a`, or `dismiss 'octo/web#12.1'` | Exits `65` with a message that gives `3` and `3.a` as the forms |
| A session directory that holds `q1.json` and `%23337.1.json`, with a `counts.json` and a `session.json`, from before the change | `list --all` omits both items, and `watch` delivers neither; the next `ask` prints `1` and writes `1.json` and `count.json`, and leaves the old files in place |
| `project inbox context --session s1` | Exits `2` |
| A subagent calls `mcp__projector__ask` | Returns `Asked 1. …`, ending `ANSWER 1 or DISMISSED 1.`; the notification is the question alone |
| A subagent calls `mcp__projector__tell` | Returns `Posted 2. …`; the notification is the notice alone |
| Send `go with 1.b` or `go with #7.1.b` in the prompt | The prompt reaches the agent with no context added, and no `project` process starts for it |
| A submit of an answer resolves `{ drop }` | The toast quotes the question's subject and names no number |
| Open the band and an item's drawer | No line or drawer shows a number, as now |

`tests/test_inbox.py` covers the CLI rows, and `hooks/inbox.test.ts` and
`hooks/band.test.tsx` cover the rest. Before the change, each row but the
last fails its test: `ask` and `tell` print `#337.1` and `q1`, the old
references parse, the old files list, `context` runs, the tools return
and notify references, a typed reference adds context, and the toast names
a reference.

### 11.9 Cost and rollout

Each `ask` and `tell` reads one small file where it read two, and runs no
`git`. A prompt you send starts no `project inbox context`, which today
runs for any prompt that holds text shaped like `#12.3` or `q2`. Nothing
else changes.

Build the change as one pull request after #295, § 10's band pull
request, merges, since both change `hooks/register.tsx`,
`hooks/inbox.ts`, their tests, and `docs/plugins.md`. The CLI and the
hooks change in that one pull request, and because `ref` stays a string,
a band and a CLI a release apart still work together.

### 11.10 What building it settled

§ 11 is built as it reads. Building it settled these points, which the
subsections above leave open or which the code had moved past since
§ 11 was written:

- **A third test read references.** #293 added a `context` check to
  `test_every_command_refuses_a_file_that_is_not_an_item`, besides
  `ContextTests` and `NoticeContextTests`, which § 11.6 names. That check
  goes with them, and the test keeps its checks of `show`, `answer`,
  `dismiss`, and `snooze`.
- **The band's `project` helper takes no standard input.** Only `explain`
  passed text to `project inbox context` on standard input, so the
  `stdin` argument of `project` in `hooks/register.tsx` goes with it, and
  so does the fake runner's in `hooks/band.test.tsx`.
- **The toast names an answer it cannot quote.** The toast for a failed
  delivery quotes the question's subject from the band's list. When the
  list no longer holds that question, it says `an answer did not reach its
  agent`, and still names no number.
- **The refusal for a number two sessions hold says where to look.**
  Every session counts from 1, so two sessions often hold a number. The
  message of exit `67` says to pass `--session` with the session that
  `project inbox list` prints beside the item.
- **The agents' instructions name a number.** The section `project init`
  writes into `AGENTS.md` tells an agent to clear an item with
  `project inbox dismiss <number>`. The text changed, so the section's
  begin marker moves to version 9.
- **A file's name still quotes its reference.** `item_file` quotes the
  reference into the file's name as it did, which leaves a number
  unchanged. A reference read from a file therefore cannot name a path
  outside the session's directory, and the band's reading of file names
  still matches the quoted names an older CLI writes.
- **What #293 and #295 changed stays.** The input checks, exit `75` for a
  busy store, the refusal of a letter by every command but `answer`, and
  #295's band, with **discuss** filling the quote and two newlines, no
  `Type something` field, a drawer that closes when you send its answer,
  and the 30 seconds a resolved line stays, all work as they did. § 11
  changes none of them.
- **A bare number looks first among open items.** Every session holds a
  `1`, a session's directory stays after it ends (§ 7), and each keeps up
  to 100 delivered items. Looking a number up in every session would make
  a bare `answer 3.a` from a shell exit `67` almost every time. So when
  neither `--session` nor `PROJECTOR_SESSION` names a session, `show`,
  `answer`, `dismiss`, and `snooze` consider only the sessions that hold
  the number open, with no answer recorded, whenever any does. An open
  item is the only kind `answer`, `dismiss`, and `snooze` can act on, and
  the one a `show` after `list` most likely means. When no session holds
  the number open, these commands look among every item as before, so
  `show` still reaches a resolved item and `answer` still says the
  question is already answered. A number open in two sessions still exits
  `67`. `claim`, `delivered`, and `reopen` act on an answered question,
  and the band and `watch` always pass its session, so they look among
  every item as before.

Each row of § 11.8 but the last failed its test before the change. The
main-screen row of § 4 still waited for a check by hand under tmux, so the
project stayed `in-progress` until the check that § 12 records.

## 12. Outcome

The project shipped, in releases 0.7.15 through 0.7.18 and in #303 and
#305, which are on `main` for the next release. #319 followed on `main`,
and § 13 records it: a reply leaves a question open, and the question's
agent can revise it. An agent posts a question or a notice with
`project inbox ask` or `project inbox tell`, or with the `ask` and `tell`
tools in Claude Code, and takes one back with `project inbox withdraw` or
the `withdraw` tool. Projector's band lists both kinds, and each item's
drawer lets you discuss, dismiss, or snooze it. The owner checked the
main-screen row of § 4 by hand under tmux. That row was the last
criterion without evidence and the check that § 6, § 9, and § 11.10 made
the gate for completion, and it also closes the main-screen row that the
[questions](../questions/readme.md) plan carried into § 4. As § 7 says,
the look of notices and snoozing that stands at this check is the design.

### 12.1 What shipped

| Release | Pull requests | What they built |
| --- | --- | --- |
| 0.7.15 | #278 | Layer 1 of § 6: `project question` becomes `project inbox`, with no new behavior |
| 0.7.15 | #279 | Layer 2 of § 6: notices, the `tell` tool, and snoozing |
| 0.7.15 | #281, #283, #284, #285, #287 | Band follow-ups: an open item's drawer as the band's top row, a blank line under the divider, the divider in a terminal only, a quote in an empty band with GitHub's errors kept out of it, and each item's kind kept when the band reads it |
| 0.7.16 | #289 | The empty band's tagline in the `subtle` color |
| 0.7.17 | #291 | The Projector section that `project init` writes tells agents to post what the person must not miss to the inbox |
| 0.7.18 | #293 | Input checks for line breaks, snoozes longer than a day, and stray answer letters, and exit `75` for a busy store |
| 0.7.18 | #295, #297, #298, #300 | The band changes of § 10 |
| 0.7.18 | #296 | The Projector section says when the inbox helps and how to clear what was answered, and `implement` asks through the `ask` tool where the tool exists |
| 0.7.18 | #294, #299 | Per-session numbering: the plan in § 11, then the build that § 11.10 records |
| On `main`, after 0.7.18 | #303 | More band changes of § 10: a pull request's drawer starts under its state, open review threads show as a yellow `⋮` count beside the age, and a sent reply that quotes a notice closes the notice's drawer |
| On `main`, after 0.7.18 | #305 | An agent takes back an item it posted with `project inbox withdraw` or the `withdraw` tool: the item leaves the band at once, and nothing goes back to the agent |
| On `main`, after #303 and #305 | #319 | § 13: only picking an option answers a question, a reply leaves the question open, and its agent withdraws it, revises it with `project inbox revise` or the `revise` tool, or leaves it open |

### 12.2 How the result differs from § 2

§ 9, § 10, § 11, and § 13 record each deviation and its reason. These
change how you use the inbox:

- **Only an option answers, and a reply leaves a question open.** A
  question's drawer has no `Type something` field (§ 10). Pressing an
  option puts the quoted question and that option in the prompt, and
  sending it answers the question. Anything else you send under a quoted
  question, such as what you type under the quote that **discuss** puts
  there, is a reply: the question stays open, and its agent withdraws it,
  revises it, or leaves it open for you to answer (§ 13).
- **A sent prompt closes the drawers it speaks to.** Sending an answer or
  a reply closes that question's drawer, and a reply that quotes a notice
  closes the notice's drawer. A resolved line stays 30 seconds, not a
  minute (§ 10, § 13).
- **Items take numbers, not references.** Each session numbers its items
  `1`, `2`, `3` across both kinds and every pull request. `project inbox
  context` and the band's reading of references in a prompt are gone, and
  a bare number looks first among open items (§ 11).
- **A snooze lasts a day at most, and a busy store exits `75`** (§ 9).
- **The band is the session's own view.** `/projector` takes no argument,
  the band lists only the loops whose watchers the session starts, the
  divider appears only in a terminal, and an open drawer has the band to
  itself (§ 9, § 10).
- **The band changed beyond the inbox.** A pull request's line ends with
  its age, after a yellow `⋮` count of its open threads, and its drawer
  starts under its state. The lines group by author, and **dismiss** drops
  a pull request from the session's loops through `project loop drop`
  (§ 10).
- **Agents learn the inbox from their instructions.** § 2.6 changes no
  skill. The Projector section that `project init` writes tells an agent
  when to post and how to clear what was answered, and `implement` asks
  through `ask` where the tool exists. The loop skills do not post through
  the inbox (§ 7).

### 12.3 Evidence

`PYTHONPATH=src python3 -m unittest discover` and `claude plugin test .`
pass at the change that completes the project. The tests below are in
`tests/test_inbox.py`, `hooks/inbox.test.ts`, and `hooks/band.test.tsx`
unless the row names another file.

| Criterion | Evidence |
| --- | --- |
| § 4: the renamed suite, and `project question list` exits `2` | `tests/test_inbox.py`, whose tests include `test_the_old_question_command_is_gone` |
| § 4: `ask` writes under `projector/inbox/` with `kind`, `postedAt`, and `subject` | `test_records_the_question_in_the_inbox_store` |
| § 4: `tell` numbers a notice and refuses bad input | `test_numbers_a_notice_in_the_count_questions_use`, `test_refuses_a_notice_that_fails_a_check_and_writes_nothing` |
| § 4: `dismiss` on a notice and on a question, `answer` and `claim` on a notice, and `watch` with only a dismissed notice | `test_dismissing_a_notice_delivers_it_at_once`, `test_dismissing_a_question_still_waits_for_its_deliverer`, `test_a_notice_takes_no_answer_and_no_claim` |
| § 4: every `snooze` row | `SnoozeTests` |
| § 4: the two `project inbox context` rows | Superseded: § 11.5 removes the command, and `test_the_context_command_is_gone` pins its exit `2` |
| § 4: the `tell` tool, the notice's line, the divider's count, and one notification | `a subagent tells through the tool, and the band lists the notice with its own bullet` |
| § 4: a notice's drawer, **discuss**, and **dismiss** | `a notice’s drawer quotes it, and dismiss clears it without sending anything` |
| § 4: **snooze** in either drawer, and a snoozed item dismissed from a terminal | `snooze hides either kind for an hour, then brings a hidden band back`, `an item dismissed from a terminal while snoozed does not return` |
| § 4, § 10: a sent prompt that quotes a notice answers nothing, and since #303 closes the notice's drawer and leaves the notice listed | `a sent prompt that quotes a notice answers nothing, and closes its drawer`, `a sent prompt answers snoozed questions and never a notice` |
| § 4: the band with no items | `an idle session with no questions starts no project process` |
| § 4: the main screen under tmux | Checked by hand by the owner |
| § 10: a question's drawer, **discuss**, an option's text, text on the quote's own line, and the drawer that closes on send | `an option puts the question and its answer in the prompt, and sending it answers the question and closes its drawer`, `discuss keeps the drawer open with the question above a blank line, a reply under it answers nothing, and dismiss resolves it` |
| § 10: a typed answer under the quote | Superseded: § 13 makes it a reply that leaves the question open, and § 13.4 gives the evidence |
| § 10: a quote followed by spaces or tabs | `a sent prompt answers each question whose quote an option follows` in `hooks/inbox.test.ts` |
| § 10: a resolved line stays 30 seconds | `a delivered question leaves the band after 30 seconds`, and the test of a notice's drawer above |
| § 10: `/projector`, and a loop that another session runs | `` `/projector` only shows or hides the band, whatever follows it ``, `draws one line per open pull request of the session’s own loops` |
| § 10: a pull request's age, in the column the items' ages share | `ends each pull request’s line with its age, in one column as wide as the widest`, `a pull request’s age and an item’s age end in one column` |
| § 10: a pull request's drawer, which since #303 starts under `draft` or `ready` | `a press on a pull request’s number opens its drawer, and discuss puts it in the prompt` |
| § 10: open review threads as a yellow `⋮` count, the last note before the age, from #303 | `draws one line per open pull request of the session’s own loops`, `a press on a pull request’s number opens its drawer, and discuss puts it in the prompt` |
| § 10: grouping by author | `lists your pull requests first, then everyone else’s by author, naming theirs before the title` |
| § 10: **dismiss** for a pull request, its refusal, and `project loop restore` | `dismiss drops a pull request from each loop that tracks it, and its line goes`, `a dismiss the CLI refuses says why and keeps the line`, and `DropTests` and `RestoreTests` in `tests/test_loop.py` |
| § 11.8: every CLI row | `NumberingTests` |
| § 11.8: the tools' replies and notifications | `asks through the tool, and the band lists the question below the pull requests`, `the tools number a session’s items in one count, and notify with the subject alone` |
| § 11.8: a number typed in a prompt | `a number or an old reference typed in the prompt reaches the agent as plain text` |
| § 11.8: the toast for a dropped submit | `a dropped submit reopens the question and says why` |
| § 11.8: no number in the band | `no line or drawer shows an item’s number` |
| #305: an agent withdraws an item it posted, which § 12.2 and § 13 rely on | `WithdrawTests`, `the withdraw tool takes an item out of the band at once, and nothing goes back to the agent`, `the withdraw tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs` |

§ 5 and § 11.9 name no measurement. They claim that no path that runs
often gains work, and the tests hold them to it: an idle band's tick
starts no `project` process, and neither does a prompt you send. Waking a
snoozed item compares times in `wokeBetween`, in `hooks/inbox.ts`, and the
inbox commands start no `git` process.

None of the code that the project replaced remains. The names it removed,
such as `project question`, `references_in`, `mayReference`,
`checkout_repo`, and `Type something`, appear only in completed plans, in
this plan, and in the fixture of
`test_leaves_items_stored_under_old_references_unread_in_place`, which
writes an old session's `counts.json` and `session.json`.

### 12.4 What this project does not deliver

These stay out of the project on purpose, and § 7 gives each one's reason
and owner:

- **A session's id outside Claude Code.** Still open.
- **The loop skills posting through the inbox.** Deferred until a
  session's id outside Claude Code is settled.
- **Views for Codex and Paseo.** Deferred for the same reason.
- **Removing old session directories.** Deferred until they turn out to
  pile up.
- **Removing old `projector/questions/` directories.** Deferred, since no
  one but the owner used `project question`.
- **Finding items across sessions.** `project inbox list --pr` is deferred
  until a view needs it.

Revising an open item in place is not among them. § 7 deferred it until
revising proved common, and § 13 (#319) took it up later for questions.
§ 13 also replaces the rule § 7 set aside, answering only under the band's
own proposals, with a different one: only an option's text answers a
question.

## 13. A reply leaves a question open

Only picking an option answers a question. Anything else you send under a
quoted question is a reply: the band records nothing, the question stays
in the band, and the reply reaches the agent as your prompt. The agent
answers what you asked and then settles the question itself: it withdraws
the question when your reply settles it, revises the question in place
when your reply shows it was unclear or the choice has changed, and
otherwise leaves it open while it still needs you. `project inbox revise`
and a `revise` tool beside `ask`, `tell`, and `withdraw` do the revising.
This section supersedes the first bullet of § 10, which made what you type
under a quoted question its answer, and settles two items of § 7: it
builds revising in place, and it replaces the rule § 7 set aside with a
different one, described below. The owner took up revising in place after
seeing replies close questions the owner had not settled, and § 12.4
records that this section takes it up.

### 13.1 Problem

Under § 10, the first line you typed under a quoted question was recorded
as its answer, under the question's last letter, and the question left the
band 30 seconds later. In daily use the owner found that wrong: "we should
not automatically remove a question when i reply to it. if i ask another
question, it may not be resolved. the correct response may be to edit or
clarify the question." A reply is often a question back, or a remark that
leaves the choice open. Recording it as the answer resolves a question
that still needs you, sends the agent `ANSWER` as if you had decided, and
leaves the agent no way to ask the question better except to post a new
one beside the old.

### 13.2 What changes

- **Only an option answers.** The band reads a sent prompt as § 10 does,
  but a quoted question takes an answer only when the text after its
  quote, up to the next quoted item or the end of the prompt, is one of
  its options, whatever blank lines, spaces, or tabs surround it. That is
  what pressing an option's number puts in the prompt. Free text, an
  option with more text after it, and a question back are replies, on
  the quote's own line or under it.
- **Each quote speaks to one question.** When two open questions share a
  subject, a quote that one's drawer put in the prompt, with an option or
  alone through **discuss**, speaks to that question: one of its options
  answers it, and any other text replies to it. When both drawers put the
  quote there, the last drawer action wins among those whose recorded
  answer is the text sent or empty, as **discuss** records it. When that
  question was answered, dismissed, or withdrawn before the prompt was
  sent, the quote answers and replies to nothing, whether the pick is
  fresh or stale, so it never falls through to the other question. A
  quote no drawer put there goes to the oldest question that offers the
  option, and otherwise replies to the oldest. A prompt that answers or
  replies to one of them says nothing to the other: it adds no note for
  the other's subagent and leaves the other's drawer open. To know which
  drawer put a quote there, the band records **discuss** as well as each
  option it puts in the prompt.
- **A reply closes the drawer and posts nothing.** A sent prompt closes
  the drawer of each question it answers or replies to, as § 10 has it
  close the drawer of each notice it quotes. A replied question stays
  open and in the band, and the band runs no `project inbox` command for
  it. When a subagent asked, the main loop reads a note beside the prompt
  that names the subagent and says to send it the reply, as it reads one
  for an answer. Once you send a prompt, the band forgets what a drawer
  put in it for each question the prompt answers or replies to, and for
  each quote the prompt holds.
- **The agent settles a replied question.** The Projector section of
  `AGENTS.md`, at begin marker 11, and the `ask` tool's description tell
  the agent that a picked option arrives as `ANSWER <number>.<letter>`,
  and that a reply arrives as the person's prompt and leaves the question
  open. The agent answers what the person asked, then withdraws the
  question, revises it, or leaves it open. The `withdraw` tool's
  description gives a question that a reply settled as its example.
- **`project inbox revise` replaces an open question's text in place.**
  `--question`, `--recommend`, `--option` (repeated, replacing every
  option but the recommendation), `--no-options`, and `--detail` or
  `--detail-file` each replace one part, and a part not named keeps its
  text. The question keeps its number, `pr`, `postedAt`, `agentId`, and
  `snoozedUntil`, so its answer still reaches the agent that asked. The
  revised question passes the checks `ask` makes. `revise` refuses, with
  exit `65`, a notice, a resolved question, an answer letter, a revision
  with nothing to change, and one that fails a check; a number no session
  holds exits `66`, and a busy store exits `75`. The write happens under
  the session's lock, as every other write does.
- **The `revise` tool runs it.** It takes the question's `number` and any
  of `question`, `recommendation`, `options`, and `detail`, where an empty
  `options` list drops every other option and an empty `detail` clears
  it. It runs `project inbox revise` through the band's `runTool`, as
  `withdraw` does, puts the revised question in the band at once, and
  raises no notification.
- **A pick made before a revision answers no question.** The band
  remembers each answer a drawer put in the prompt, and each **discuss**,
  with the question's quote as it read then. A revision marks the
  question's picks stale. A sent prompt that holds a stale pick, its old
  quote followed by its option, answers nothing, not even an older
  question that shares the old words and offers that option. Neither does
  an option typed under a quote that a stale **discuss** put there. Each
  replies to the revised question instead: the question stays open, its
  drawer closes, and the agent reads the prompt. A fresh pick or
  **discuss** in the revised question's drawer speaks to it again, even
  when the revision kept that quote and that option.

### 13.3 Why this design

- **A reply is not a decision.** An option is a full answer that the
  agent wrote and the person chose. Free text may decide, ask, or object,
  and the band cannot tell which. Leaving the question open and letting
  the agent, which reads the reply, settle it puts the judgment where the
  understanding is. A reply that does decide costs the agent one
  `withdraw` call.
- **The rule keys on an option's text, not on the band's proposal.** § 7
  set aside a rule under which a quote answered only while the band held a
  proposal for it, because § 10 meant typed text to answer. The rule here
  answers whenever the text is an option's, proposal or not, so an option
  you type by hand, or send through Remote Control, still answers, and a
  proposal only decides between questions that share a subject.
- **A stale pick is kept, not dropped.** An option you picked in one
  question's drawer must never answer a different question. Dropping the
  pick at the revision would leave its text to the rule above, which
  gives it to the oldest question that shares the quote and offers the
  option. Keeping it, marked stale, lets the band recognize the text and
  hold it back. For the same reason, the band matches a pick whatever has
  happened to its question since, and a pick whose question was resolved
  before you sent the prompt holds its text back from every question.
- **`revise` is its own command, and the tool's name is `revise`.** § 7
  sketched `project inbox ask --revise <number>`. `ask` posts a new item
  and prints a new number; revising keeps the number and refuses what
  `ask` never meets, a resolved item and an unknown number, with the exit
  codes `withdraw` uses, so it reads as a command beside `withdraw`.
  `edit` would collide with `project edit`, which opens a plan in an
  editor.
- **Notices are not revised.** The need is a question that a reply showed
  to be unclear. A notice that has gone stale is withdrawn, and the agent
  tells again; revising one would need its own flag for the notice's line
  and its own tool field, for no case anyone has met.
- **Neither `revisedAt` nor `pr` changes.** § 7's sketch recorded
  `revisedAt` and let a revision change `pr`. Nothing reads a revision
  time, and the reply that prompts a revision is about the question's
  wording, not its pull request, so both wait until something needs them.
- **A typed answer stays in the CLI.** `project inbox answer <number>.<last
  letter> --text` still records an answer in your own words from a
  terminal, where nothing mistakes a reply for an answer, and the drawer
  still shows such an answer after the options, marked `✓`.

### 13.4 Evidence

| Behavior | Test |
| --- | --- |
| Free text, an option with more text after it, a question back, and another question's option answer nothing; an exact option answers, whitespace and a following quoted item included | `a sent prompt answers each question whose quote an option follows`, `anything after a quoted question but one of its options replies to it without answering`, `a quote two questions share goes to the oldest that offers the option it is followed by`, in `hooks/inbox.test.ts` |
| A quote two open questions share speaks to one of them: an answer to one is no reply to the other, a reply goes to the question whose drawer put the quote there, and the drawer acted in last wins | `a quote two questions share speaks to one of them: an answer replies to neither, and a reply goes where its drawer put it`, in `hooks/inbox.test.ts`; `a prompt about one of two questions that share a subject leaves the other alone`, `a quote two questions share goes to the one whose drawer you acted in last`, in `hooks/band.test.tsx` |
| A sent reply leaves the question listed, closes its drawer, runs no `project inbox answer`, and names the subagent that asked | `a sent reply leaves the question listed, closes its drawer, and records no answer, naming the subagent that asked`, `discuss keeps the drawer open with the question above a blank line, a reply under it answers nothing, and dismiss resolves it`, in `hooks/band.test.tsx` |
| The `revise` tool redraws the question with its new text under the same number, and a pick made from the old text answers nothing | `the revise tool redraws the question with its new text at once, keeping its number` |
| A pick made before a revision answers no question, an older question with the same words included, and replies to the revised question; a fresh pick from the new drawer answers it | `a pick made before its question was revised answers no question, and replies to the revised one`, in `hooks/inbox.test.ts`; `an answer chosen before a revise answers no question, and a pick from the new drawer answers the revised one`, `an answer chosen before a revise rewords its question does not answer another question with the old words` |
| A pick or **discuss**, fresh or stale, whose question was answered, dismissed, or withdrawn before the prompt was sent answers and replies to nothing | `a quote a drawer put in the prompt for a question resolved before you send speaks to no question`, in `hooks/inbox.test.ts`; `a pick for a question withdrawn before you send answers no other question`, `a pick made before a revise, for a question dismissed before you send, answers no other question` |
| An option typed under a quote that **discuss** put there before a revision answers no question and replies to the revised question; a pick in another question's drawer still answers that one | `an option typed under a quote discussed before its question was revised answers no question, and replies to the revised one`, in `hooks/inbox.test.ts`; `an option typed under a quote discussed before a revise answers no question, and replies to the revised one` |
| The `revise` tool refuses what the CLI refuses | `the revise tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs` |
| `project inbox revise` replaces what it is given and keeps the number and everything else; refuses a resolved question, a notice, an unknown number, and bad input, changing nothing; serializes under the lock | `ReviseTests` in `tests/test_inbox.py` |
| The Projector section's new text is pinned to begin marker 11 | `test_the_template_text_is_pinned_to_its_version` in `tests/test_instructions.py` |

## 14. Restore a dismissed or snoozed item

You can bring back an item you dismissed or snoozed, in place, under its
number. `project inbox restore <number>` reopens it, the band's drawer offers
**restore** for a dismissed item and **unsnooze** for a snoozed one, and an
agent restores an item with a `restore` tool beside `ask`, `tell`,
`withdraw`, and `revise` when you say a dismissal was a mistake. Dismissing
or snoozing an item from the band now closes its drawer, and the item stays
listed, muted, for 30 seconds, which is the window in which its drawer offers
to bring it back. This section supersedes the bullet of § 2.5 under which a
snoozed item leaves the list at once and a dismissed notice's drawer keeps
only **discuss**.

### 14.1 Problem

The owner dismissed a question by accident, and nothing brought it back.
The only recourse was for the agent to ask the question again, which posted
a duplicate under a new number beside the dismissed one. The owner said: "we
need a way to undismiss. creating a duplicate question isn't good." Two more
gaps showed in the same use. When the owner pressed **dismiss**, the drawer
stayed open over the band on an item that no longer needed anything: "when
it is dismissed, we should close the drawer". And a snoozed item left the
band at once, so a snooze pressed by mistake could be undone only from a
terminal, with `project inbox snooze <number> --for 0`. The owner asked for
"a restore button in place of dismiss when you open a dismissed question"
and for "unsnooze when an item is snoozed".

### 14.2 What changes

- **`project inbox restore <number>` reopens an item in place.** It takes
  `--session` and `--json` as the other commands do, and works under the
  session's lock. A dismissed item loses `answer`, `deliveredAt`, and any
  `claim`, which is everything dismissal and its delivery recorded, and
  keeps its number, text, options, `pr`, `postedAt`, and `agentId`. A later
  `answer` then records an answer as if the item had never been dismissed.
  A snoozed item loses `snoozedUntil` and `snoozedAt` and nothing else.
  `restore` refuses, with exit `65`, an open item, an answered question, a
  withdrawn item, and an answer letter; a number no session holds exits
  `66`, and a busy store exits `75`. Without a session, it looks first among
  the dismissed and snoozed items, as `answer` looks first among open ones.
- **A restore tells the agent only what it may have heard.** A dismissed
  question owes its agent `DISMISSED`. When no deliverer has claimed that
  line, the restore cancels it, and nothing goes out. When the line was
  delivered or claimed, the restore records `restoredAt`, and the deliverer
  sends `RESTORED <number> <subject>`, the shape of the other lines, so the
  agent knows the question is open again. `watch`, `claim`, and the band's
  deliverer handle it as they handle `ANSWER` and `DISMISSED`. A snooze and
  a notice's dismissal send the agent nothing, so restoring either sends
  nothing.
- **A change to a line in flight drops its claim.** A restore drops the
  claim on a `DISMISSED` line being delivered, and an answer or a second
  dismissal drops the claim on a `RESTORED` line being delivered. The
  deliverer's `delivered` or `reopen` then exits `65`, and the next
  deliverer sends what the question owes then. `watch` carries on past such
  a refusal. An answer replaces a `RESTORED` line nobody has claimed, since
  the answer tells the agent the question was open. A second dismissal
  before anyone claimed `RESTORED` is delivered at once, since the last line
  the agent heard already says `DISMISSED`. A claimed `RESTORED` is done
  after `delivered` or `reopen`, so no deliverer sends it twice.
- **Dismissing or snoozing closes the drawer.** In the band, **dismiss** and
  **snooze** close the item's drawer, as sending an answer or a reply does.
  A dismissed item stays listed for 30 seconds after `deliveredAt`, muted
  and struck through, as before. A snoozed item stays listed for 30 seconds
  after the new `snoozedAt`, which `snooze` records, in the `subtle` color
  but not struck through, so it reads as put off rather than dismissed, and
  out of the divider's counts. Then it leaves the band until its snooze ends.
- **The drawer offers the way back where it offered the way out.** While
  such an item is listed, its line opens its drawer as before. A dismissed
  item's drawer reads `discuss | restore`, with **restore** in **dismiss**'s
  place and no **snooze**, since a resolved item cannot be snoozed. A
  snoozed item's drawer reads `discuss | dismiss | unsnooze`. Either
  action runs `project inbox restore`, redraws the item open at once, and
  leaves the drawer open on it, with `discuss | dismiss | snooze` again.
- **A restored item brings a hidden band back, as a new item does.** The
  band shows itself for any item that is open and not snoozed where the
  list it last held had it otherwise: a new item, or one restored or
  unsnoozed from a terminal. The `restore` tool shows a hidden band itself,
  since it puts the item in the list before the band reads the store. Before,
  only a `ref` the band had never seen counted, so an item the person had
  asked an agent to bring back stayed out of sight behind a hidden band.
- **A drawer closes when its line leaves the band.** The band closes the
  open drawer once its pull request or item is no longer listed, on each
  tick and each reload. Before, the drawer's key stayed set, so a line that
  came back, a pull request restored to a loop or an item restored or woken,
  came back with its drawer open. **dismiss** in a pull request's drawer
  already closed it when every drop succeeded.
- **The `restore` tool runs it.** It takes the item's `number`, runs
  `project inbox restore` through `runTool`, and puts the item in the band
  at once, as `withdraw` does. Its description tells the agent to use it
  only when the person says a dismissal was a mistake or asks for the item
  back, never on its own judgement, and never to ask a dismissed question
  again as a new one. The `ask` tool's description says that `RESTORED`
  arrives when the person restores a question its agent heard dismissed.
- **Agents learn it from their instructions.** The Projector section of
  `AGENTS.md`, at begin marker 12, tells the agent to restore an item the
  person says they dismissed by mistake rather than ask again.
- **Codex allows it by name.** `project init` adds `restore` to the `inbox`
  subcommands its Codex rules allow. Claude Code's `Bash(project *)` rule
  already allows it, and it takes no ask rule.

### 14.3 Why this design

- **Restore in place, not ask again.** A duplicate leaves two items for one
  decision, with the dismissed one still in `list --all` and the agent
  holding two numbers. Restoring keeps the number the agent already holds,
  so `ANSWER` and `DISMISSED` keep arriving under it.
- **One command for both ways back.** Restoring a dismissed item and ending
  a snooze both answer "bring it back", so one command and one tool cover
  them, and an agent never has to know which the person pressed. The band's
  button reads **unsnooze** for a snoozed item because that is what it does
  there. `snooze --for 0` still wakes an item from a terminal, so a separate
  `unsnooze` command would add a third spelling for no new case.
- **Cancel when unheard, tell when maybe heard.** An agent that never heard
  `DISMISSED` has nothing to correct, so the restore sends nothing. Once the
  line was delivered, or claimed and possibly sent, the agent may hold the
  question as dismissed, and `RESTORED` corrects it. A duplicate `RESTORED`
  for a question the agent still held open is harmless; a missing one would
  leave the agent ignoring a question the person wants answered.
- **Drop the claim rather than mark it.** Recording in each claim which line
  it carries would let `delivered` tell an old line from a new one, but
  every deliverer would have to carry it. Dropping the claim when the
  question changes keeps `settle`'s rule as it was: a claim that still
  matches settles the line it was taken for. A deliverer delivers one line
  of a question at a time, so the band and `watch` still send `RESTORED`
  after the `DISMISSED` it corrects.
- **`RESTORED` is told once.** A `reopen` of a `DISMISSED` line reopens the
  question so the person answers again, but there is nothing for the person
  to redo after a failed `RESTORED`. Retrying it would repeat the band's
  toast every five seconds while delivery keeps failing. The question stays
  open, and its eventual answer or dismissal reaches the agent.
- **A withdrawn item is the agent's.** The agent took it back, so restoring
  it would put back an item its own agent decided it no longer needed. The
  agent posts it again if it changes its mind. An answered question is not
  restored either: its answer went to the agent, and `restore` is for a
  mistaken dismissal.
- **A dismissal's snooze is not restored.** Dismissing clears the snooze, as
  § 2 has it, so `restore` returns the item open and in view, which is what
  someone undoing a dismissal wants.
- **`snoozedAt` lives in the store.** The 30 seconds a snoozed item stays
  listed count from the snooze, as a dismissed item's count from
  `deliveredAt`. Recording the time in the item, rather than in the band's
  memory, gives a snooze from a terminal the same window and survives a
  reload of the module.
- **No ask rule for `restore`.** `answer`, `dismiss`, and `snooze` ask before
  an agent runs them because they record the person's choice. `restore`
  undoes a choice the person tells the agent was a mistake, and puts the
  item back in front of the person, so it cannot answer anything for them.

### 14.4 Evidence

| Behavior | Test |
| --- | --- |
| `restore` brings back a dismissed question exactly as it was, keeps the number, cancels the undelivered `DISMISSED`, and a later `answer` works | `test_restores_a_dismissed_question_in_place_and_cancels_its_undelivered_dismissal` in `RestoreTests`, `tests/test_inbox.py` |
| A dismissed notice comes back and sends nothing | `test_restores_a_dismissed_notice_and_sends_nothing` |
| `RESTORED` goes out only after `DISMISSED` was delivered or claimed, once | `test_sends_restored_only_after_its_agent_heard_dismissed`, `test_a_restore_while_dismissed_is_being_delivered_sends_restored_after_it`, `test_a_restored_line_whose_delivery_failed_is_not_sent_again` |
| A change to a line in flight drops its claim, and `watch` carries on | `test_watch_survives_a_restore_that_lands_while_its_line_goes_out`, `test_an_answer_or_dismissal_before_restored_goes_out_replaces_it`, `test_dismissing_again_once_restored_is_claimed_sends_dismissed_after_it` |
| A snoozed item loses its snooze and nothing else | `test_restores_a_snoozed_item_by_clearing_its_snooze` |
| The refusals, the bare number, and the lock | `test_refuses_an_open_answered_or_withdrawn_item_and_an_unknown_number`, `test_a_bare_number_looks_first_among_dismissed_and_snoozed_items`, `test_two_restores_at_once_restore_once`, `test_a_lock_another_process_holds_makes_a_restore_wait_then_exit_75`, and `restore` in `test_refuses_an_answer_letter_on_every_other_command` |
| **dismiss** closes the drawer and strikes the line through; the drawer then reads `discuss \| restore`; **restore** redraws it open with **dismiss** back, sends `RESTORED` only after `DISMISSED`, and a pick answers it | `dismiss closes an item’s drawer and strikes it through, and its drawer offers restore in dismiss’s place` in `hooks/band.test.tsx`, and `a notice’s drawer quotes it, and dismiss clears it without sending anything` |
| **snooze** closes the drawer and leaves the line in `subtle`, not struck, for 30 seconds; the drawer reads `discuss \| dismiss \| unsnooze`, and **unsnooze** brings `snooze` back | `snooze closes an item’s drawer and mutes it for 30 seconds, and its drawer offers unsnooze in snooze’s place`, `snooze hides either kind for an hour, then brings a hidden band back`, and `a snoozed item stays in the band for 30 seconds after it is snoozed, then leaves until its time` in `hooks/inbox.test.ts` |
| A restore from the tool or a terminal brings a hidden band back | `a restore brings a hidden band back, from the tool or from a terminal, as a new item does` |
| A drawer closes when its line leaves the band, so a returning line has it closed | `a pull request’s drawer closes when its line leaves the band, so it does not open again when the line returns`, and the restore tool's test below |
| The `restore` tool redraws the item at once, its old drawer closed, refuses what the CLI refuses, and a failed `RESTORED` is said once | `the restore tool brings a dismissed item back at once, its old drawer closed, and its agent hears RESTORED`, `the restore tool refuses what the CLI refuses, asks an old CLI to upgrade, and says when it hangs`, `a dropped RESTORED says so without asking for an answer, and is not sent again` |
| The tools' descriptions and arguments | `the restore tool is for a dismissal the person calls a mistake, never a question asked again`, `` a call of the restore tool becomes `project inbox restore`, and only a whole number names an item ``, `the ask tool tells the agent that a pick arrives by number and a reply as the person’s prompt`, `word that a question was restored waits for a deliverer until a live claim holds it`, in `hooks/inbox.test.ts` |
| Codex allows `restore` by name | The `project inbox restore` row of `DECISIONS` and `test_the_codex_rules_name_every_command_or_leave_it_out` in `tests/test_permissions.py` |
| The Projector section's new sentence is pinned to begin marker 12 | `test_the_template_text_is_pinned_to_its_version` in `tests/test_instructions.py` |

## 15. Use the band from the keyboard

You can work the band from the keyboard as you answer Claude Code's own
surveys. An open question's options are Buttons on the digits `1` to `6`,
and a digit presses one from the band or typed first into an empty prompt.
A drawer's options and actions form one list, a line each, and each
action takes a letter by its place: `d` for **discuss**, `x` for
**dismiss** or **restore**, and `s` for **snooze** or **unsnooze**.
Enter on a line opens its drawer and moves the focus ring into it, and
Enter on the open line closes the drawer with the ring on the line.
`docs/plugins.md` gains a section on the band's keys. This section
supersedes the bullets of § 2.5 that draw a drawer's actions on one row,
`discuss | dismiss | snooze`; the bullet of § 10, "Each drawer link has a
line of its own, and the actions come last", for a pull request's drawer,
whose actions now come before its links; and the last row of § 4's table,
under which an option's number takes no press on the main screen.

### 15.1 Problem

Claude Code moves the keyboard into the band with ctrl+x tab, walks a focus
ring over the band's Buttons with Tab and the arrows, and presses the one
under the ring with Enter. A question's options did not take part. Each was
a `Markdown` link, `1.`, which only a click presses, so on the main screen,
which Claude Code uses under tmux and takes no clicks, a question could be
answered only by typing its quote and option by hand. Enter on a line
opened its drawer but left the ring on the line, and the engine keeps the
ring by position, so after an item's drawer took the band to itself the
ring sat on whatever element held the line's old place. Every action took
Tab presses and Enter. The owner asked for the band to work as a survey
does: digits for the options and letters for the actions.

### 15.2 What changes

- **Options are Buttons on digits.** An open question's options are `plain`
  Buttons with the hotkeys `1` to `6`, in the order of their letters, the
  recommendation first with its `(recommended)` mark. The engine draws the
  digit before the option in the accent color, as `1: Decline it`, so the
  band draws no number of its own. Pressing an option, by a click, Enter,
  or its digit, does what clicking its number did: it records the pick
  through `propose` and fills the prompt with the quote and the option, so
  `readPrompt` reads the sent prompt as before.
- **The digits work from an empty prompt.** Claude Code presses a band
  Button whose hotkey is a digit typed first into an empty prompt, as it
  answers a survey, while the Button is wholly inside the band's visible
  window, so the digits are set whenever an open question's drawer is
  open. A digit only fills the prompt, so a stray one sends
  nothing.
- **A resolved question's options are text.** Its drawer lists the options
  with no digits and nothing to press, the answer given at full strength
  and marked `✓`, the rest in `subtle`, and an answer typed with
  `project inbox answer --text` after them.
- **Options and actions form one list.** The `discuss | dismiss | snooze`
  row is gone. Each action is a line of its own under the options, after a
  blank line: a `plain` Button whose letter the engine draws before its
  label in the accent color. A resolved question's text options take the
  same blank line under them, a notice's actions follow its detail after
  one, and the lengths snooze offers keep it above them. A pull request's
  drawer puts its actions directly under its facts line and its links
  under its actions, with no blank line anywhere, as the owner sketched it.
- **A keyed line draws in two colors.** On an option or an action line, the
  key takes the engine's accent color, `suggestion`, and the `: ` and the
  label take the default text color. The label is a plain string, so an
  open question's `(recommended)` mark loses its `success` color on the
  option line; a resolved question's text options keep it.
- **Actions take letters by their place.** **discuss** takes `d`, the
  second place takes `x`, whether it holds **dismiss** or **restore**, and
  the third takes `s`, whether it holds **snooze** or **unsnooze**.
- **Each label names what it acts on.** A question's drawer reads
  `d: discuss question`, `x: dismiss question`, and `s: snooze question`,
  and a notice's reads `notice` in place of `question`. A dismissed item's `x`
  line reads `x: restore question` or `x: restore notice`, and it has no
  `s` line, since the CLI refuses to snooze a resolved item. A snoozed
  item's `s` line reads `s: unsnooze question` or `s: unsnooze notice`. A
  pull request's drawer reads `d: discuss PR` and `x: dismiss PR` under
  its facts line, with its links, which take no keys, below the actions.
  The owner first had `d: discuss this` on every drawer, then asked for
  `d` to name what it discusses, as `x` and `s` do, and for `PR` in place
  of `pull request` to keep the pull request's lines short.
- **The letters always show.** A letter presses its action only while the
  band holds the keyboard; typed in the prompt, it is text.
- **Enter takes the ring into the drawer.** A `ui.focus` hook records the
  band element the ring last landed on. When a line is pressed with the
  ring on it, the press came from Enter, and the band moves the ring with
  `$.ui.focus`: onto a question's first option when a drawer opens, or
  onto **discuss** when the drawer has no options to press. Enter on an
  open item's line moves the ring back onto the item's line in the list. A
  pull request's drawer opens and closes under its line, which keeps its
  place, so closing it leaves the ring where it is. A click moves nothing.
- **An open item's line has a key of its own.** While its drawer is open,
  an item's line is keyed `close:<number>`, not `item:<number>`, so the
  move back onto the line waits for the band to draw the list again.
- **A refused move is logged.** When the engine refuses a move, or the call
  fails, the band writes `projector: the focus stayed off <key>: <reason>`
  to the debug log.
- **A ctrl-click on an option no longer opens the pull request.** The link
  that carried the option went with it. The pull request's own drawer
  links it, and the owner accepted the loss.

### 15.3 Why this design

- **Survey keys.** The owner answers Claude Code's surveys with a digit
  from an empty prompt, and the band's questions are the same kind of
  choice, so they take the same keys. Filling the prompt rather than
  answering keeps § 13's rule that only a sent prompt answers.
- **`1` to `6`, not `1` to `5`.** The `ask` tool takes a recommendation and
  up to five other options, six answers in all.
- **Letters by place, not by name.** **restore** and **unsnooze** undo what
  **dismiss** and **snooze** did, in the places they held, so each keeps
  the letter of what it undoes. Only one drawer is open at a time, so no
  two actions share a letter. `x` stands for **dismiss** because `d` is
  **discuss**.
- **What the engine says about the keyboard.** `AbovePrompt`'s render
  props carry no focus state, unlike a `Pane`'s `isFocused`. `ui.focus`
  fires before the ring moves: on ctrl+x tab, which puts the ring on the
  band's first element; on a click on the band outside its Buttons; on Tab
  and the arrows; and on `$.ui.focus`. Esc and a click into the prompt
  move nothing the band hears, and the band is not drawn again. So the band
  learns when it takes the keyboard but not when it loses it, and cannot
  show the letters only while it holds it, as the owner first asked.
- **Every action is a line, its letter drawn.** A `plain` Button with a
  hotkey always draws the hotkey before its label, a Button without
  `plain` draws `[ label ]` in bold, and no prop hides the hotkey, so the
  engine offers no way to keep a letter undrawn on a plain label. The owner
  chose to make the letters part of the drawer: options and actions as one
  list a key each, like a survey's rows.
- **The label takes the `: `'s color.** The engine draws a plain Button as
  one `Text`: the hotkey in a nested `Text` fixed to `suggestion`, then
  `: ` and the label. Its own `Text` takes no color from the plugin; only a
  `hover` override colors it, and only while the pointer is over the
  Button, never under the focus ring. Hover or focus inverts that `Text`,
  so each part with its own color inverts into a block of its own. The
  owner first had the actions' labels in `subtle`, then asked for the whole
  line in one color, and found the `: ` in a third color the oddest part.
  Neither the key nor the `: ` can take the label's color, so the label
  takes the `: `'s, the default text color: the line draws and inverts as
  the key's block and one block after it. `suggestion` is the same color
  as `permission`, lavender on the default dark theme.
- **The ring tells Enter from a click.** `ui.press` does not say what
  pressed a Button. A click on a Button presses it without moving the ring
  or taking the keyboard, and Enter presses the Button under the ring, so a
  press on the line the ring is on is the keyboard's. The record can be
  stale after Esc, but the engine refuses `$.ui.focus` for a band that
  does not hold the keyboard, so a stale record moves nothing.
- **`close:<number>` rather than a timer.** The engine keeps the ring by
  position, and an open item's drawer has the band to itself, so the item's
  line is the top row while the drawer is open and lower in the list once
  it closes. `$.ui.focus` resolves a key against the band as drawn when
  the key is already drawn, and waits for the next drawing only when it is
  not. A key the open line does not share waits for the list, with no
  timing to guess.
- **Tests read the debug line.** Claude Code's test kit serves no
  `$.ui.focus` and lets no test hook see a plugin's call, so a test raises
  `ui.focus` to place the ring and reads which element the band then tried
  from the debug line it writes when the kit refuses the move.

### 15.4 Evidence

| Behavior | Test |
| --- | --- |
| An open question's options are plain Buttons on `1` to `6` with no number of the band's; the digits are set only while its drawer is open; pressing the sixth fills the prompt and sending it answers; a resolved question's options have no digits | `an open question’s options are Buttons on the digits 1 to 6, only while its drawer is open`, `an option puts the question and its answer in the prompt, and sending it answers the question and closes its drawer` in `hooks/band.test.tsx` |
| A pick through a Button speaks to its question as a pick through the link did | The tests § 13.4 lists, which now press the Buttons |
| A drawer's actions follow its options a line each, after a blank line and with no `|`, the blank line kept above snooze's lengths and under a resolved question's options; they take `d`, `x`, and `s` by place, labelled by kind, **restore** and **unsnooze** included, with no `s` line on a dismissed item; a pull request's drawer reads `discuss PR` and `dismiss PR` between its facts line and its links; no line of the band takes a letter | `a drawer’s actions follow its options a line each, on d, x and s, named for what they act on`, `a press on a pull request’s number opens its drawer, and discuss puts it in the prompt`, `a notice’s drawer quotes it, and dismiss clears it without sending anything` |
| A keyed line's label is a plain string with no color, `hover`, or `dimColor`, so it takes the `: `'s color | `` a keyed line’s label takes the color of the `: ` before it, so it inverts as one block `` |
| Enter on a line moves the ring into the drawer it opens and back onto an item's line; a click moves nothing; a pull request's drawer closes in place | `Enter on a line moves the ring into the drawer it opens and back onto the line, and a click moves nothing` |
| The ring on a live terminal, ctrl+x tab, the digits from an empty prompt, and the drawer's lines | Checked by hand by the owner in a preview session |

## 16. Smaller changes to the band

The owner asked for these changes to the band while previewing § 15,
most to how it draws its lines and one to how **snooze** works. They are
recorded apart from § 15 so they can ship apart. This section supersedes
the bullet of § 2.5 that gives a notice the bullet `•`; its bullet under
which **snooze** runs `project inbox snooze` at once, which snoozes for the
CLI's default hour; and the part of § 10's bullet on ages that shows open
review threads as a count after `⋮`.

### 16.1 What changes

- **The links read `open in github` and `open in projector`.** A pull
  request's drawer drew them as `github` and `projector`, which read as
  labels rather than as things to press. No other drawer draws these
  links: an item names its pull request as text, and § 15 removed the link
  that carried a question's options.
- **A pull request's facts take a color per piece.** The line under a
  pull request, such as `watching · fix-branch · +12 −3 in 2 files`, was
  drawn whole in `subtle`, which made the drawer's main content its
  faintest line. Now each loop takes a color of its own, from the
  constant `LOOP_COLORS`: `watching` the terminal palette's blue,
  `ansi256(4)`, and `reviewing` the theme's `claude` orange, rgb(215,119,87)
  on the dark and light themes, rgb(255,153,51) on the daltonized ones, and
  `ansi:redBright` on the ANSI ones. A pull request both loops track reads
  `watching and reviewing`, each word in its own color and ` and ` in the
  default color. The branch takes the palette's cyan, `ansi256(6)`, in the
  constant `BRANCH_COLOR`. The size takes the colors the next bullet but
  one gives it, and each ` · ` between them keeps the default color.
- **Only the checks take the checks' color.** The facts line ended with
  ` · checks pending` drawn whole in the checks' color, so the `·` turned
  yellow while checks ran. The `·` now keeps the line's color, and only
  `checks <state>` takes `success`, `error`, or `warning`.
- **The size reads as a diff stat.** In the facts line's
  `+12 −3 in 2 files`, the insertions take `success` and the deletions
  `error`, green and red as a diff stat draws them, and ` in 2 files` keeps
  the default color, after the owner tried it in `subtle`. The theme's
  `diffAdded` and `diffRemoved` are backgrounds for whole diff lines, not
  text colors, so the band uses `success` and `error`. The owner tried
  the terminal's `gray`, the drawer links' color, and `subtle` for the
  branch before choosing cyan. Claude Code resolves a `Text` color only as
  a theme key, `rgb()`, hex, `ansi256()`, or an `ansi:` name, and draws a
  plain name such as `gray` or `cyan` in the default color; and the band's
  surface refuses a color with a `:`, so a plugin cannot use `ansi:cyan`,
  the form Claude Code's ANSI themes use. `ansi256(6)` passes both: it
  names the palette's cyan slot, so it follows the person's palette as
  `ansi:cyan` would. `ansi256(14)`, bright cyan, is the change to make if
  slot 6 reads too dark. Watching's blue takes the same form, slot 4.
- **A pull request's open threads read `5⋮`.** The count of unresolved
  review threads comes before the `⋮` rather than after it, still in
  yellow, `warning`, and still the last note before the age.
- **Questions and notices share one bullet, and an open question takes a
  color.** Every item's line starts with `-`. An open question's line, its
  bullet and its subject, is drawn in the theme color `permission`, and an
  open notice's keeps the default text color. A resolved or snoozed item
  is drawn in `subtle` as before, and a dismissed one is struck through.
  The color lives in one constant, `QUESTION_COLOR`, in
  `hooks/register.tsx`.
- **Snooze asks how long.** Pressing **snooze** in an item's drawer
  snoozes nothing. It replaces the action lines with
  `15 min | 1 hour | 4 hours | 1 day | cancel`, plain Buttons on one row,
  `|` between them. A length runs
  `project inbox snooze <number> --for <minutes>` with 15, 60, 240, or
  1440, and **cancel** puts the actions back. The question lives in the
  open drawer only, in the `bandSnoozing` atom keyed by the drawer: closing
  the drawer or opening another puts the actions back. The lengths take no
  hotkeys. Enter on **snooze** moves the ring onto the first length, and
  Enter on **cancel** moves it back onto **snooze**, as § 15 moves it for a
  drawer. **unsnooze** still acts at once.

### 16.2 Why this design

- **A color tells the kinds apart better than a glyph.** `-` and `•` look
  alike at a glance, and the difference between the kinds that matters is
  that a question waits on the owner. A color draws the eye to it.
- **`permission` is the question's color.** Claude Code draws its own
  permission prompts in `permission`, and an agent's question usually asks
  leave to do something, so the band reuses the color the owner already
  reads that way. In Claude Code's built-in themes it is a lavender or
  light blue on dark backgrounds, a stronger blue on light ones, and
  `ansi:blue` or `ansi:blueBright` in the ANSI themes, distinct from the
  text color and from `subtle` in every one. The owner tried `inactive`,
  which made a question recede, and `text`, before choosing it.
- **The notice keeps the default color.** A notice needs only a read, so
  it stays as plain as a pull request's title.
- **Snooze asks rather than assuming an hour.** The owner pressed
  **snooze** expecting to be asked how long, and the item left the band on
  the default hour. Asking first costs one more press, and nothing is
  snoozed until a length is picked, so a press by mistake costs nothing.
  The lengths stay within the CLI's cap of a day. They take no digits,
  because the digits belong to a question's options.

### 16.3 Evidence

| Behavior | Test |
| --- | --- |
| The drawer's links read `open in github` and `open in projector`; on the facts line watching takes `ansi256(4)` and reviewing `claude`, each word its own when both loops track it, the branch `ansi256(6)`, the size's `+N` `success` and its `−N` `error`, `checks <state>` the checks' color, and ` in N files`, ` and `, and each `·` stay plain; the open-thread count reads `2⋮`, in `warning`, after the other notes | `a press on a pull request’s number opens its drawer, and discuss puts it in the prompt`, `opening one drawer closes the one that was open`, and `lists a pull request both loops track once, as watched and reviewed` in `hooks/band.test.tsx` |
| Every item's line starts with `-`; an open question's takes `permission`, inverted while its drawer is open; an open notice's takes no color; resolved, dismissed, and snoozed items take `subtle`, the dismissed one struck through; nothing uses `dimColor` | `` every item takes the `-` bullet, an open question the permission color, and a resolved or snoozed one subtle ``, `a subagent tells through the tool, and the band lists the notice in the default color` |
| **snooze** shows the lengths and cancel, runs nothing, and takes no digits; each length runs `snooze --for` with its minutes; **cancel** and closing the drawer put the actions back; Enter carries the ring onto the first length and back onto **snooze** | `snooze asks how long, and only a length it offers snoozes the item`, and the snooze tests § 14.4 lists, which now pick a length |
| A restored question's line returns to `permission` | `dismiss closes an item’s drawer and strikes it through, and its drawer offers restore in dismiss’s place`, `the restore tool brings a dismissed item back at once, its old drawer closed, and its agent hears RESTORED` |

## 17. Close an item's drawer on 0

An open item's drawer gives the band to the item, its own line at the top,
and that line closes the drawer. The line now takes the hotkey `0`, which
the engine draws before it as `0: `. Pressing `0` closes the drawer as a
click or Enter on the line does, so the keyboard can close a drawer from
anywhere in it, not only with the ring on its line.

### 17.1 What changes

- **An open item's line takes `0`.** Every inbox item's drawer gets it:
  an open or resolved question, an open, dismissed, or snoozed notice,
  and a snoozed question. The line is the `close:<number>` Button § 15
  keys, so the hotkey is set only while the drawer is open; a closed
  item's line in the list takes none.
- **`0` works as the option digits do.** It presses the line while the
  band holds the keyboard, and typed first into an empty prompt while the
  drawer is open and the line is wholly inside the band's visible window,
  as Claude Code takes a digit for a survey. A `0` typed while a tall
  drawer is scrolled past its line reaches the prompt as text.
- **Closing is unchanged.** The press runs the line's `toggle`, so it
  closes the drawer and moves the focus ring exactly as a click or Enter
  on the line does: the ring follows the line back into the list only when
  it was on the line.
- **A pull request's drawer takes no `0`.** Its number's Button opens and
  closes it in place, and it never has the band to itself.

### 17.2 Why this design

- **`0`, not `/` or `.`.** The owner asked for `/` and then `.`, but a
  Button's hotkey must be one digit or one lowercase letter, and the
  engine refuses anything else. The options take `1` to `6`, so `0` is
  free in every drawer, and it sits beside them on the keyboard.
- **The line keeps its look.** The engine draws the `0` in its accent
  color, `suggestion`, and the `: ` in the Button's own text color, before
  the line's bullet and subject. Those keep their colors and stay
  inverted while the drawer is open, so the line still reads as the open
  item. An open question's line is drawn in `permission`, the same color
  as `suggestion` in every built-in theme, so its `0` matches it.

### 17.3 Evidence

| Behavior | Test |
| --- | --- |
| No line of the list and no pull request's drawer takes `0`; each open item's line takes it, open, answered, dismissed, or snoozed, and pressing it closes the drawer | `an open item’s line closes its drawer on 0, and no other line takes 0` in `hooks/band.test.tsx` |
| The only digit in a resolved question's, a notice's, or a snoozing drawer is the line's `0` | `an open question’s options are Buttons on the digits 1 to 6, only while its drawer is open`, `an option puts the question and its answer in the prompt, and sending it answers the question and closes its drawer`, `snooze asks how long, and only a length it offers snoozes the item` |
| `0` from an empty prompt and the line's look with the prefix | Checked by hand by the owner in a preview session |
