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
   The line stays dim for a minute and then leaves the band, as an answered
   question does. A question's drawer works as it does now, with **snooze**
   added after **dismiss**.
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
first:

```text
──────────────────────────── 1 question · 1 notice · updated 12s ago [-]
#337 draft Retry failed webhook deliveries with backoff

- Decline the finding about the retry budget?                     #337  5m
• Force-pushed #337 to drop a stray debug file                    #337  1m
```

- **A notice's line.** The same line as a question's, with the bullet `•`
  in place of `-`, so a glance tells which items want an answer and which
  want a read. The bullet and the subject are one plain `Button` that opens
  the drawer.
- **The divider** counts open questions and open notices separately,
  `1 question · 2 notices`, leaving out a count of zero and any snoozed
  item.
- **A notice's drawer.** A blank line, its detail as `Markdown` and another
  blank line when it has detail, then `discuss | dismiss | snooze`: three
  plain `Button`s, each drawn as its label alone, with a dim `|` between
  them, in place of the bracketed buttons the drawer draws today.
  **discuss** puts the quoted subject and a space in the prompt, as it does
  for a question. **dismiss** runs `project inbox dismiss`, and **snooze**
  runs `project inbox snooze`; each puts the item the command prints in the
  band's list, and a second press finds the item changed and does nothing.
- **A question's drawer** ends the same way, `discuss | dismiss |
  snooze`, gaining **snooze** after **dismiss**.
- **A snoozed item** leaves the list and closes its drawer. On each tick,
  shown or hidden, the band checks for an open item whose `snoozedUntil`
  has passed since its last tick; such an item returns to the list, and the
  band appears if you hid it. Waking needs no `list`, because the band
  already holds the item.
- **A dismissed notice's line.** Dim and struck through for a minute after
  `deliveredAt`, then gone. Its drawer still opens, with only **discuss**.
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
  band's reading of the store, its list, its drawer, and its minute of
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
  once, retention, `watch`, the band's deliverer, and the band's minute of
  fading all handle a dismissed notice with no new branch: it is simply a
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
| Press **dismiss** in a notice's drawer | Runs `project inbox dismiss`; the line turns dim and struck through; no `$.session.send` or `$.prompt.submit` follows; the line leaves the band a minute later |
| Press **snooze** in either kind's drawer | Runs `project inbox snooze`; the item leaves the list and the divider's count; with the band hidden, an hour later the item returns and the band appears |
| Snooze an item, hide the band, and dismiss the item from a terminal | An hour later the item does not return and the band stays hidden |
| Press **discuss** in a notice's drawer | The prompt gains the quoted subject and a space |
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
checked by hand; until then layer 2 leaves it `in-progress`.

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
  `answer`, and `dismiss`. Deferred until `project inbox` ships.
- **Old session directories.** A session's directory under
  `projector/inbox/` stays after the session ends. Removing directories
  older than some age is deferred until they turn out to pile up.
- **The look of notices and snoozing.** The `•` bullet, the divider's
  wording, the hour a snooze lasts, and whether the band says how many
  items are snoozed are a first choice. Owner: ninjudd, to adjust in a
  development copy of the band once layer 2 lands, as the questions' layout
  was.
- **Old `projector/questions/` directories.** Left on disk, unread.
  Removing them is deferred, since no one but the owner has used the
  command.

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

Both layers of § 6 are built. Building them settled these points, which the
sections above leave open or state loosely:

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
  band.
- **`--for 0` ends a snooze now rather than removing it.** It sets
  `snoozedUntil` to the current time, so the band's next tick sees the item
  wake and brings back a band you hid, as it does when a snooze runs out.

The main-screen row of § 4 waits for a check by hand under tmux, so the
project stays `in-progress`.
