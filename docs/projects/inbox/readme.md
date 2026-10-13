---
status: in-progress
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
  seconds of fading all handle a dismissed notice with no new branch: it is simply a
  delivered item.
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

The main-screen row of § 4 waits for a check by hand under tmux, so the
project stays `in-progress`.

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
  it open too, so the options and detail stay in view while you type.
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
  band last loaded, stays.
- **A pull request's drawer starts under the number.** Its lines start in
  the row's first column, not where the titles start.
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
| `hooks/register.tsx` | **discuss** puts the quoted subject and two newlines in the prompt; a question's drawer draws no `Input`, and a resolved question keeps the `✓` row of its typed answer; `/projector` drops its `argumentHint` and its handling of a tracked file, and its description names the inbox; `runningLoops`, the `ps -A` scan, goes, and `load` lists only the seen loops that have a kind; the toast for a tracked path the band cannot read says that the loop is not listed; muted text takes the `subtle` color; a sent prompt that answers a question closes its drawer; a watcher's entry replaces a seen loop that has no kind |
| `hooks/parse.ts`, `hooks/parse.test.ts` | `expandPath`, which only `/projector <tracked file>` used, goes with its test |
| `hooks/types.d.ts` | `SeenLoop.kind` is required |
| `hooks/inbox.ts` | `answersIn` lets spaces or tabs follow a quote before its line break; `RESOLVED_MS` is `30_000` |
| `hooks/inbox.test.ts`, `hooks/band.test.tsx` | The rows below |
| `docs/plugins.md` | The drawer, **discuss**, which quotes answer, the 30 seconds, and the band without a file named by hand |
| `hooks/register.tsx` | Each pull request's line ends with its age, in a column shared with the items' ages through one helper, and its title grows to fill the line; the drawer drops "updated … ago" and `drawerIndent`, and puts **github** and **projector** on lines of their own |
| `docs/plugins.md` | The band examples, the age at the end of a pull request's line, and the drawer under the number with one link per line |
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
| Press **discuss** on a notice, type under the quote, and send | Nothing is recorded; the prompt reaches the agent |
| Press an option's number, then send the prompt | The drawer stays open after the press and closes when the prompt is sent; the band shows its list |
| Press **discuss**, type an answer under the quote, and send | The drawer stays open while you type and closes when the prompt is sent |
| Send a quoted question, two spaces, a line break, and an option's text | The band records that option |
| Answer or dismiss an item | Its line stays, in the `subtle` color, for 30 seconds after `deliveredAt`, then leaves the band |
| Run `/projector` while another session runs a loop | The band shows or hides, and lists only the loops whose watchers this session started |
| Look at a pull request's line | It ends with how long ago the pull request changed, such as `7h`, in the `subtle` color, in the same column as the items' ages |
| Open a pull request's drawer | Its lines start under the number; its facts name no author and end with the checks; **github** and **projector** each have a line, and the row with **discuss** comes last |
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
   other command refuses `3.a` with exit `65`. Without `--session`, a
   command looks the number up in every session, as it does now. Once two
   sessions hold a `3`, it exits `67` and names both, and you pass
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
request, merges, since both change `hooks/register.tsx`, `hooks/inbox.ts`, their
tests, and `docs/plugins.md`. The CLI and the hooks change in that one
pull request, and because `ref` stays a string, a band and a CLI a
release apart still work together.
