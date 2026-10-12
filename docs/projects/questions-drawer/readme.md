---
status: ready
priority: now
---

# Answer an agent's questions in Projector's band

An agent posts a question to its session's band above the Claude Code
prompt and keeps working; you answer it there, and the answer goes straight
to the agent that asked.

Projector's band is the Claude Code hooks module in `hooks/`. It lists the
open pull requests of your fix and review loops one line each, opens a
drawer under a line when you click it, and `/projector` shows or hides it,
as `docs/plugins.md` describes. This project adds the session's questions to
the same band, each beside the pull request it is about. It uses the command
and the band-prefixed state keys that
[#269](https://github.com/ninjudd/projector/pull/269) gives the band, so its
implementation follows #269.

## 1. Problem

When a loop's subagent needs a decision from you, it has nowhere to put it
but the conversation. `start-fix-loop` and `start-review-loop` both say "a
question for the user comes back to the main loop to ask", and the subagent
goes on with other work meanwhile. The main loop then asks in the transcript,
as a line of text. `review-pr` and `summarize-pr` do the same when they need
you to run a command they cannot, such as `project init` or a refused
publish.

That line is easy to miss, and nothing remembers it:

- **It scrolls away.** A loop's session also carries every watcher
  notification and subagent report, so a question asked an hour ago is far
  above the prompt.
- **Nothing lists what is still open.** No view says which questions are
  waiting, how old they are, or which pull request they hold up.
- **The answer has no route back.** The main loop must remember to relay
  your answer to the subagent that asked. Claude Code's `AskUserQuestion`
  tool has a route, but it blocks the turn with a dialog, which stops a loop
  that should keep working.

Each unanswered question holds up the pull request it is about, often for
hours, because a loop finishes everything else it can and then waits.

## 2. Solution

The hooks module registers a tool, `ask`, that the model calls as
`mcp__projector__ask`. Every question carries the agent's recommended
answer, and may offer choices, of which the recommendation must be one. The
tool records the question in the session's own plugin state and returns at
once, so the agent keeps working. The session's band lists its questions as
a checklist, each on the line after the pull request it is about and each
open one marked `[ ]`, with the open count in the divider. Clicking a
question opens its drawer: its detail, then each choice as a bulleted line
with the recommended one marked, a field for any other answer, and
**dismiss**. When you answer, the module delivers the answer at once, to the
subagent that asked or, when that subagent has ended, to the session's main
loop. Once the answer was taken, the line reads `[x]` with the answer for
one minute, then leaves the band. An answer that was not taken reopens the
question, so an answer is never lost. Each session has only the questions
its own agents asked, and you answer them only in that session.

### 2.1 What happens when an agent asks

1. An agent, the main loop or a subagent, calls `mcp__projector__ask` with
   `question` and `recommendation`, and optionally `options` and `pr`.
2. The module's `tool.call` hook answers the call itself. It refuses the
   call with `isError` and the reason, so the agent asks again, when
   `question` is more than one line or longer than 120 characters, when
   `recommendation` is empty, or when `options` are given and
   `recommendation` is not one of them. Otherwise it adds the question
   (§ 2.2) to the session's `bandQuestions` state, with the calling
   subagent's `agentId` when there is one. It shows the band, raises one
   `$.ui.notify` through your notification channel, and returns `Posted
   question <id>. Keep working; the answer arrives as a message.`
3. The band puts each question on the line after the pull request its `pr`
   names, oldest first when a pull request has several, and starts it at
   the same left edge as the pull request lines. A question with no `pr`,
   or one about a pull request the band does not list, goes above the pull
   requests. An open question's line starts `[ ]`. The divider counts the
   open questions: `──── 2 questions · updated 12s ago [-]`.
4. You open a question's drawer by clicking its line in the fullscreen
   layout, or on the main screen by focusing the band with ctrl+x tab and
   pressing Enter on the line. Opening any drawer closes any other, a pull
   request's included.
5. You answer by pressing the recommended answer, another option, by typing
   in the field and pressing Enter, or by pressing **dismiss**. The module
   records the answer on the question, so its line reads `sending` and a
   second press does nothing, and delivers it:
   - When the question has an `agentId` and `$.agent.list()` reports that
     subagent `pending`, `running`, `waiting`, or `idle`, the module sends
     the answer with `$.session.send({ to: { agentId } })`. The list keeps
     an ended subagent too, as `completed`, `failed`, or `killed`, so the
     status decides, not the listing.
   - Otherwise, or when the send resolves `{ isDelivered: false }`, the
     module queues the answer for the main loop with `$.prompt.submit`,
     naming the subagent when one asked, so the main loop relays it as the
     loop skills already do.
6. When the send resolves `{ isDelivered: true }`, or the submit resolves
   without `drop`, the module sets the question's `resolvedAt` and closes
   its drawer. Its line then reads `[x]` and the answer, and the band's
   5-second tick removes it one minute after `resolvedAt`. When the submit
   resolves `{ drop }` or either call rejects, the module clears the
   recorded answer, so the question is open again, and raises a toast with
   the reason. You can answer it again.

A dismissed question is delivered the same way, as `You dismissed question
<id>`, so the agent stops waiting for it.

### 2.2 A question

Each question is one entry in `bandQuestions`, a list in the session's
`$.state` under the `projector` plugin.

| Field | Value |
| --- | --- |
| `id` | `q-` and the time in base 36, then four random base-36 characters |
| `askedAt` | When the agent asked, in milliseconds |
| `agentId` | The asking subagent's id, from the `tool.call` input; absent for the main loop |
| `question` | The question in one line of at most 120 characters, the checklist line's text |
| `detail` | What you need to answer it, as Markdown, shown only in the drawer; empty when the line says it all |
| `recommendation` | The answer the agent recommends; one of `options` when it has them |
| `options` | Up to six answers, in order, each a sentence or more when it needs one; empty when the recommendation is the only choice |
| `pr` | The pull request it is about, as `owner/repo#number`; null when none |
| `answer` | Absent while open; `{ text }` or `{ dismissed: true }` from the moment you answer, kept after delivery for the `[x]` line |
| `resolvedAt` | Absent until the answer was taken; then when, in milliseconds |

### 2.3 The `ask` tool

The module registers the tool in `session.start` with `$.tool.register`.

| Field | Value |
| --- | --- |
| `name` | `ask`, which the model sees as `mcp__projector__ask` |
| `description` | Post a question for the person without stopping your work. Use it when you need a decision you cannot make, such as declining a finding or running a command you are not allowed to run. Ask in one short line, put what the person needs to decide in `detail`, and always give the answer you recommend; when you offer options, write each as a full answer the person can pick as it stands, and recommend one of them. Keep working on anything the answer does not block; the answer arrives later as a message. |
| `inputSchema` | `question` and `recommendation`, required strings; `detail`, a string; `options`, an array of at most six strings; `pr`, a string matching `owner/repo#number` |
| `isDeferred` | `false`, so the tool is in the model's tool list from the start rather than behind ToolSearch |

### 2.4 What the band draws

The questions are a checklist with one line each. An open question's line
is a `Button` with `plain`, so it is a press target in both layouts: a click
in fullscreen, and Enter once the band has the keyboard on the main screen.
It reads `[ ]`, the question, truncated only on a narrow terminal, then its
age, dim. A question above the pull requests also names its `pr`, when it
has one, before its age; one under its pull request does not repeat it. A
resolved question's line is plain dim text that takes no press: `[x]`, the
question, and the answer, or `dismissed`.

```text
──────────────────────────────────────── 1 question · updated 12s ago [-]
[x] Run project init to add the publish rule?          → done · 40s
#267 draft List a loop's open pull requests above the Claude Code prompt
[ ] Decline the finding about the retry budget?        5m
    The review asks for a retry budget per host. The loop already caps
    retries at three in total, which covers the case the finding names.
    - Decline it: the total cap already bounds retries to one host (recommended)
    - Add a budget of two retries per host, as the review asks, and keep the
      total cap of three
    [ Answer…                                                          ]
    [ dismiss ]
#258 ready Name the reviewed head in a review's signature line
```

The drawer holds what the line has no room for: `detail` as `Markdown`,
then the answers, each choice a line of its own. Each choice is a `Button`
with `plain` whose text starts `- `, and a long one wraps onto further
lines under its own text. The recommended choice ends `(recommended)`, in
the accent color. A question without options draws one choice, its
recommendation, so you accept the recommendation the way you pick any
choice. After the choices come an `Input` keyed `answer`, which takes any
other answer through its `onSubmit`, and a **dismiss** button, each on a
line of its own.

The choices have no hotkeys. Claude Code presses a band `Button` whose
hotkey is a digit when that digit is typed first in an empty prompt, the
way a survey takes its answer, so a prompt such as `2 things first:` would
answer the question. Without hotkeys, only a click, or Enter on a choice
while the band holds the keyboard, picks an answer. The drawer's
open/closed state is the band's `bandDrawer`, keyed `q:<id>` for a
question, so one drawer is open at a time across questions and pull
requests.

### 2.5 Files

| File | Change |
| --- | --- |
| `hooks/questions.ts` | New: the question's id and type check, the checks a question must pass (one line, a recommendation, one of the options), the order, the route an answer takes from the subagent's status, and the text each delivery sends |
| `hooks/register.tsx` | Register `ask` in `session.start`; answer its `tool.call`; deliver an answer, then resolve or reopen its question; remove a resolved question a minute later on the tick; draw the checklist, its drawer, and the open count in the divider |
| `hooks/types.d.ts` | The `Question` type, and `bandQuestions` under the `projector` plugin's state |
| `hooks/questions.test.ts` | New: the id, each check a question must pass, the order, and the route by the subagent's status |
| `hooks/band.test.tsx` | Asking, each refused question, answering by the recommendation, an option, the field, and dismiss, each delivery route and each failure, a second press while sending, the `[x]` line for a minute, a digit typed in the empty prompt answering nothing, and the divider's open count |
| `docs/plugins.md` | A section on questions in the band |

No skill changes. The tool exists only where Claude Code loads the hooks
module, and its description tells an agent when to use it, so the shared
skills keep working in Codex and without the module.

## 3. Why this design

- **A tool, not a pattern in the transcript.** The band could look for a
  marker in the model's text, such as `needs input:`, but text is easy to
  get almost right and hard to answer back to. A tool call has a schema,
  the caller's `agentId`, and a result the agent reads.
- **Posting, not blocking.** `AskUserQuestion` waits for an answer in a
  dialog. A loop must keep fixing and reviewing what the question does not
  block.
- **A recommendation with every question.** You can settle most questions
  by accepting the agent's answer, and an agent that must name one has
  thought the decision through before it asks. Requiring the recommendation
  to be one of the options, when there are options, lets the drawer mark it.
- **Choices as bulleted lines, not a row of buttons.** A choice worth
  offering often needs a sentence to say what it does, which a button label
  cannot hold, and a line each lets it say that in full. The recommendation
  is a choice too, so a question without options still answers the same
  way, with one press on one line.
- **One line, then the drawer.** A checklist of one line each shows every
  open question at a glance and keeps the band short, while `detail` holds
  whatever the decision needs, out of the way until you open it.
- **Resolved lines stay a minute.** The `[x]` line shows that the answer
  went through, and what it was, without the list growing.
- **Each session's own questions.** A question belongs to the session whose
  agent asked it, and an `agentId` means something only in that session. So
  the questions live in the session's `$.state`, which survives a hot
  reload of the module and redraws the band whenever it is written, and
  nothing crosses to another session's band.
- **Delivered when answered.** You answer in the session that asked, which
  knows whether its subagent still runs, so the module delivers at once
  rather than leaving the answer for a later pass to find.
- **Kept until taken.** A send can come back undelivered and a submit can
  be dropped by a hook, so the question stays until one of them took the
  answer, and a failure reopens it.
- **Beside its pull request, at the same edge.** A question about a pull
  request is about something on that pull request's line, so it reads best
  right after it, without repeating the number. Starting it at the same left
  edge keeps every line's `[ ]` or number in one column, and the `[ ]` alone
  marks it as a question. A question with no listed pull request has no
  line to follow, so it goes above them all.
- **Buttons, not links, for a question's line.** The pull requests' numbers
  are `Markdown` links, which take a click only in the fullscreen layout,
  as `docs/plugins.md` says. A `Button` also takes Enter when the band has
  the keyboard, so questions work on the main screen too.
- **Built on the band, not beside it.** The band above the prompt has one
  slot, which the first hook that draws there fills, so a second band cannot
  sit beside it. Questions reuse the band's divider and its `bandDrawer`
  state.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| A subagent calls `mcp__projector__ask` | The call returns at once with the question's id; `bandQuestions` holds the question with that subagent's `agentId`; the band shows it as a `[ ]` line and the divider counts it |
| Ask about a pull request the band lists | The question's line comes right after that pull request's line, at the same left edge, without the pull request's number; two questions about it follow it oldest first |
| Ask with no `pr`, or about a pull request the band does not list | The question's line comes above the pull requests, naming its `pr` when it has one |
| A call whose `question` has two lines or 121 characters, whose `recommendation` is empty, or whose `recommendation` is not one of its `options` | The call returns `isError` with the reason, and no question is added |
| Open a question with options | Its drawer shows `detail`, then each option as its own line starting `- `, a long one wrapping under its text, and the recommended one ending `(recommended)` |
| Open a question without options, and press its one choice | The choice is the recommendation, marked `(recommended)`, and the agent receives it as the answer |
| Press an option, or type an answer and press Enter | The subagent receives the answer through `$.session.send`; the line reads `[x]` and the answer, the divider no longer counts it, and it leaves the band one minute later |
| Press a resolved question's line | Nothing happens |
| Answer after the subagent has ended, so `$.agent.list()` reports it `completed`, `failed`, or `killed` | The main loop receives the answer through `$.prompt.submit`, naming the subagent |
| `$.session.send` resolves `{ isDelivered: false }` | The main loop receives the answer through `$.prompt.submit` |
| `$.prompt.submit` resolves `{ drop }`, or rejects | The question is open again, a toast gives the reason, and answering again delivers |
| Press an option twice before the first delivery settles | The agent receives one answer |
| Press **dismiss** | The agent receives `You dismissed question <id>`; the line reads `[x]` and `dismissed`, and leaves the band one minute later |
| A question's drawer is open, and you type `2 things first:` in the empty prompt | The question stays unanswered, and the prompt holds what you typed |
| The band is hidden when a question arrives | The band appears |
| Another session's band | Shows none of this session's questions |
| On the main screen, press ctrl+x tab and Enter on a question | Its drawer opens, and typing reaches the answer field |
| No questions | The band and its divider look as they do today |

`hooks/questions.test.ts` and `hooks/band.test.tsx` cover every row but the
main-screen one, answering `agent.list`, `session.send`, and `prompt.submit`
from memory. Check the main-screen row by hand under tmux.

## 5. Cost

- **The band.** A question adds one write to the session's state when it is
  asked, one when it is answered, one when it resolves, and one when the
  band's 5-second tick removes it a minute later. The tick already runs, and
  the check is a comparison of times; the 30-second GitHub poll is
  unchanged.
- **The tool's schema.** With `isDeferred: false`, the schema rides in
  every request: a name, a short paragraph of description, and five
  properties.

## 6. Open questions

- **Opening a pull request's drawer on the main screen.** Its number is a
  `Markdown` link, which takes no press there. Switching pull request lines
  to `Button`s, as questions use, would fix it but loses the ctrl-click that
  opens the Projector summary. Owner: ninjudd, after trying question lines
  on the main screen.
- **A resumed session.** Whether Claude Code restores a plugin's `$.state`
  when you resume a session is undocumented. If it does not, the questions
  are gone from the band, while each asking agent's tool call stays in its
  transcript. Deferred until it is observed.
- **Codex.** Codex has no band and no hooks module, so its agents ask in
  the transcript as they do today. Deferred.
