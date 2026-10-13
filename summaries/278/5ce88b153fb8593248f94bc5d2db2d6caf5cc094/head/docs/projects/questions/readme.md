---
status: completed
priority: now
---

# Answer an agent's questions from any host

An agent asks you a question through `project question ask` and keeps
working; you answer it from Projector's band in Claude Code, or from the
CLI anywhere, and the answer goes back to the agent that asked.

The `project` CLI owns the questions, so every host reaches the same ones:
Claude Code, Codex, and a runner such as [Paseo](https://paseo.sh). In
Claude Code, the questions also appear in Projector's band above the prompt,
which `hooks/` draws and `/projector` shows or hides
([#269](https://github.com/ninjudd/projector/pull/269) names it), each
beside the pull request it is about. The implementation follows #269.

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
  that should keep working, and Codex and Paseo have routes of their own or
  none.

Each unanswered question holds up the pull request it is about, often for
hours, because a loop finishes everything else it can and then waits.

## 2. Solution

A new `project question` command keeps each session's questions as files
under Projector's state directory, and every host asks, lists, answers, and
hears answers through it. A question is one line, carries the agent's
recommended answer, and may offer other choices. `project question ask`
checks it, numbers it, and prints its reference at once, so the agent keeps
working: `#337.3` for the third question on #337, `q2` for the session's
second question about no pull request. Its answers are lettered: `a` is
always the recommendation, then the other choices in the agent's order, and
the last letter is a typed answer. You answer with `project question answer
#337.3.a`, or in Claude Code by sending a prompt that a question's drawer in
the band fills with the question and your answer. The store records the
answer, and one deliverer claims it and sends it to the agent that asked: in
Claude Code the band, which delivers every answer in its session, and
anywhere else `project question watch`, which any host's watcher can run. An
answer you send in a prompt needs no deliverer, because the prompt carries it
to the main loop. A reference you type in a prompt reaches the agent with its
question and choice attached.

The work is in two parts. § 2.1 to § 2.4 build the CLI that every host
uses. § 2.5 to § 2.7 build Claude Code's view over it in Projector's band.

### 2.1 The CLI

Every command takes `--session <id>`, the asking session's id, and reads
`PROJECTOR_SESSION` when the flag is absent. A host passes the id it already
has: in Claude Code, the `ask` tool passes `$.session.id()`, and a skill can
write `${CLAUDE_SESSION_ID}`. You, answering from a terminal, need no id:
`list` without a session lists every session's open questions with their
sessions, and `show`, `answer`, and `dismiss` without one look the reference
up in every session. Every command takes `--json` for a script or a view, as
the rest of the CLI does.

| Command | What it does |
| --- | --- |
| `project question ask <question> --recommend <answer> [--option <answer>]… [--detail <markdown> \| --detail-file <path>] [--pr <owner/repo#number>] [--agent <id>]` | Checks the question (§ 2.3), numbers it, writes it, and prints its reference |
| `project question list [--all]` | Lists open questions, and with `--all` delivered ones too |
| `project question show <reference>` | Prints one question with its detail and lettered answers |
| `project question answer <reference>.<letter> [--text <answer>]` | Records an answer: a letter, or the last letter with `--text` |
| `project question dismiss <reference>` | Records that you dismissed it |
| `project question claim <reference> [--renew <token>]` | Claims a recorded, undelivered answer for delivery and prints a token and the text to deliver; with `--renew`, keeps that claim live (§ 2.2) |
| `project question delivered <reference> --claim <token>` | Marks a claimed answer delivered |
| `project question reopen <reference> --claim <token>` | Clears a claimed answer whose delivery failed, so the question is open again |
| `project question watch [--interval <seconds>] [--once]` | Claims each undelivered answer, prints one line for it, marks it delivered, and keeps watching, or with `--once` makes one pass and exits; with `--json`, prints one JSON object a line |
| `project question context` | Reads prompt text on standard input and prints the context for each reference in it (§ 2.4) |

`ask` exits `65` with the reason when a check fails, so the agent asks
again. `answer` and `dismiss` exit `65` when the question already has an
answer. `claim` exits `65` when the answer has a live claim or is
delivered, and `claim --renew`, `delivered`, and `reopen` exit `65` when
the token is not the answer's claim. A lapsed claim's token still works
until another `claim` replaces it, so a deliverer paused past the minute
can still record what it delivered. A command exits `66` when the
reference names no question, and `67` when, without a session, it names
questions in more than one session.

`watch` prints each answer as one line an event, the shape
`watch-threads.sh` uses, so Claude Code's Monitor, a Codex loop, or a Paseo
runner hears it:

```text
ANSWER #337.3.a Decline the finding about the retry budget? → Decline it: the total cap already bounds retries to one host
ANSWER #337.3.c Decline the finding about the retry budget? → keep the cap, but log each retry
DISMISSED #337.3 Decline the finding about the retry budget?
```

### 2.2 The store

A session's questions live in
`$XDG_STATE_HOME/projector/questions/<session>/`, or under
`~/.local/state/projector/questions/` when `XDG_STATE_HOME` is unset, beside
`project review`'s `reviews/` (`state_dir` in `src/projector/review.py`).
The directory holds one JSON file per question, `counts.json`, the last
number given in each count, and `session.json`, the session's repository
once it has one (§ 2.3). A command that writes first takes the session's
lock, the operating system's exclusive lock on the directory's `.lock` file
(`flock` on Linux and macOS, `msvcrt.locking` on Windows). It then writes
each file to a temporary name before renaming it into place, so two writers
never lose each other's change and a reader never sees half a file. The
system releases the lock when its holder exits, so a command that dies
holding it blocks no other command. A command that finds the lock held
retries for 5 seconds, then exits `65`.

| Field | Value |
| --- | --- |
| `ref` | Its reference, fixed when the agent asks: `#337.3`, `owner/repo#337.3`, or `q2` |
| `number` | Its count, the last part of its reference |
| `pr` | The pull request it is about, as `owner/repo#number`; null when none |
| `askedAt` | When the agent asked, in milliseconds |
| `agentId` | The asking subagent's id, from `--agent`; absent for the main loop |
| `question` | The question in one line of at most 120 characters |
| `detail` | What you need to answer it, as Markdown; empty when the line says it all |
| `choices` | The answers in letter order: the recommendation first, then the other options in the agent's order |
| `answer` | Absent while open; then `{ letter, text, answeredAt }` or `{ dismissed: true, answeredAt }` |
| `claim` | Absent until a deliverer claims the answer; then `{ token, renewedAt }`, the claim's random token and when it was last renewed, in milliseconds |
| `deliveredAt` | Absent until the answer reached the agent; then when, in milliseconds |

Every answer reaches its agent the same way, wherever you gave it. It is
recorded first, by `answer` or `dismiss` from a terminal or from a view.
Then one deliverer claims it: `claim` writes a new token and `renewedAt`
under the lock only when the answer has neither a live claim nor
`deliveredAt`, and prints the token with the text to deliver, so two
deliverers never both send it. Only that token renews the claim, marks it
`delivered`, or `reopen`s it when the send failed. While a send or submit
is pending, which can take minutes behind a busy main loop, the deliverer
renews the claim every 5 seconds with `claim --renew <token>`. A claim not
renewed for one minute counts as abandoned, so only a deliverer that
stopped leaves the answer for the next one. In Claude Code the band is the
deliverer (§ 2.7); anywhere else, `watch` is, for whichever host runs it.

A session keeps its 100 most recently delivered questions, so a reference
to one still resolves after it leaves every view, and drops older ones as it
writes. The store is keyed by the session's id, so a session you resume
finds its questions where it left them.

### 2.3 Numbers, letters, and the checks

A question's number is its reference, and an answer's letter extends it.
Each reference takes one form when the agent asks, and keeps it:

- **A question about a pull request in the session's repository** is `#`,
  the pull request's number, `.`, and its count among that pull request's
  questions in the session: `#337.1`, `#337.2`. The session's repository is
  the `owner/name` in the `origin` remote of the working directory where
  the session's first `ask` from a Git checkout runs. The session records
  it then and keeps it, so a later `ask` from another repository's
  checkout cannot give that repository's #337 the same short reference.
- **A question about a pull request in another repository** spells the
  repository out: `owner/repo#337.3`. Two repositories that each have a
  #337 never share a reference.
- **Any other question** takes the next number in the session's own count,
  after a `q`: `q1`, `q2`, `q3`.
- **An answer** is the question's reference, `.`, and its letter. `a` is
  always the recommendation, so `#337.3.a` means "do what you recommend".
  The other options follow as `b`, `c`, and on, in the agent's order. The
  last letter is always a typed answer, so a question with a recommendation
  and one other option has `c` for a typed answer.

A count never goes back, so a reference names one question for the whole
session. `ask` refuses a question that is more than one line or longer than
120 characters, an empty recommendation, more than five other options, or
an option that repeats the recommendation.

### 2.4 References in prompt text

`project question context` finds references in text by exactly these forms,
each standing alone: not touching a letter, a digit, `/`, `#`, or `.` on
its left, and ending at a space, a line break, punctuation other than `.`,
or the end of the text. A `.` ends a reference too when a space, a line
break, or the end of the text follows it, so a reference can close a
sentence.

| Form | Matches | Does not match |
| --- | --- | --- |
| `#<pr>.<n>`, with an optional `.<letter>` | `#337.3`, `#337.3.b`, and `#337.3.b` in `Go with #337.3.b.` | `#337`, `#337.3.4`, `x#337.3` |
| `<owner>/<repo>#<pr>.<n>`, with an optional `.<letter>` | `octo/app#337.3.b` | `app#337.3` |
| `q<n>`, with an optional `.<letter>` | `q2`, `q2.a`, and `q2.a` in `Let's do q2.a.` | `2`, `Q2`, `eq2`, `q2.4` |

For each reference that names a question the session holds, open or
delivered, it prints the question and, for an answer's reference, that
choice. Any other text that looks like a reference passes through.

### 2.5 Claude Code: the `ask` tool

The hooks module registers `ask` in `session.start` with `$.tool.register`,
so the model sees it as `mcp__projector__ask`, with `isDeferred: false` so
it is in the tool list from the start. It is a tool the module serves
itself, not an MCP server. The tool takes `question`, `recommendation`,
`detail`, `options`, and `pr`. Its description tells the agent to ask in one
short line, put what you need to decide in `detail`, always recommend an
answer, write each option as a full answer you can pick as it stands, and
keep working on anything the answer does not block.

Its `tool.call` hook runs `project question ask` with `$.process.run`,
passing `--session` from `$.session.id()` and `--agent` from the call's
`agentId`. It answers with the printed reference, or refuses with `{ deny:
reason }`, which the model reads as an error result. When the command exits
`65`, the reason is the check that failed. On any other exit, such as `2`
from a `project` too old to know `question`, the reason is the command's
error and `run project upgrade`. When `project` cannot start, or is still
running when `$.process.run` gives up after 15 seconds, the reason says it
did not finish and tells the agent to ask in the conversation instead. When
the command succeeds, the hook also shows the band and raises one
`$.ui.notify`. An agent can run `project question ask` itself from the shell
too, which reaches the same store.

### 2.6 Claude Code: what the band draws

```text
──────────────────────────────────────── 2 questions · updated 12s ago [-]
#337 draft Retry failed webhook deliveries with backoff
#258 ready Name the reviewed head in a review's signature line

- Run project init to add the publish rule?                             2m
- Decline the finding about the retry budget?                     #337  5m
```

With a question's drawer open:

```text
──────────────────────────────────────── 2 questions · updated 12s ago [-]
- Decline the finding about the retry budget?                     #337  5m

  The review asks for a retry budget per host. The loop caps retries at
  three in total, which covers the case the finding names.

  1. Decline it: the total cap already bounds retries to one host
     (recommended)
  2. Add a budget of two retries per host, and keep the total cap
  3. [ Type something                                                    ]

  [ discuss ] [ dismiss ]
```

- **Where a question sits.** The questions are a bulleted list below the
  pull requests, after a blank line, oldest first. A question shows no
  reference. Each line ends with the question's pull request, as the band
  labels it when it lists it and in full otherwise, then its age, both in
  the `subtle` color; the ages share one right-aligned column, so the pull
  requests end in one column too. The divider counts the open questions.
- **An open question's line.** The bullet and the question are one plain
  `Button`, which draws as ordinary text and takes a press anywhere on it: a
  click in fullscreen, and Enter once the band has the keyboard on the main
  screen. Pressing it opens the question's drawer, and opening any drawer
  closes any other, through the band's `bandDrawer`. While its drawer is
  open the question draws inverted, as the pointer draws it, and it has the
  band to itself: the other questions and the pull requests return when the
  drawer closes or the question leaves the band.
- **The drawer.** A blank line, `detail` as `Markdown` and another blank
  line when there is detail, then the options, numbered from `1.`, one line
  each. Each number is a `Markdown` link, drawn as a pull request's number
  is, in a column of its own beside the option's text. A plain click on it
  puts the question and that option in the prompt (§ 2.7); a ctrl, alt or
  cmd click opens the question's pull request, or the question's own file
  in the store when it has none, since `Markdown` draws only an `https:`,
  `http:` or `file:` link as one. The first option, the recommendation,
  ends `(recommended)` in the `success` color. The last number holds an
  `Input` keyed by its answer's reference, such as `answer:#337.3.c`, with
  the placeholder `Type something`, and Enter there fills the prompt the
  same way; the mobile app draws no `Input`, so there the drawer leaves
  that line out. After a blank line come **discuss** and **dismiss**, both
  buttons. Nothing in the band has a hotkey: Claude Code presses a band
  `Button` whose hotkey is a digit when that digit is typed first in an
  empty prompt, so a hotkey would let `2 things first:` press it.
- **An answered question's line.** The same line, dim, and struck through
  for a dismissed question. Until the answer is delivered, `sending`
  follows the question. Its drawer still opens, with the options dim and
  the one given, or the text typed, marked `✓` in the `success` color, and
  nothing to press but **discuss**. The line leaves the band one minute
  after `deliveredAt`.

### 2.7 Claude Code: answers and references

1. **The band reads the store when it changes, shown or hidden.** On its
   5-second tick it lists the session's question directory with `$.fs.list`,
   whether the band is shown or not, and runs `project question list --all
   --json` only when a file's name, size, or time changed since the last
   read that succeeded, so an idle session starts no process and a read
   that fails runs again on the next tick. A file whose claim the band
   itself is renewing counts by its name alone, so the band's own renewals
   never make it read the store again. A question it has not shown before
   shows the band, so one asked from the shell appears as surely as one
   asked through the tool.
2. **Your prompt answers the question.** An option or a typed answer puts
   the question, in quotes, then a blank line, then the answer's text into
   the prompt at the cursor, and changes nothing in the store, so you can
   send it as it stands, change it, or answer several questions in one
   prompt. **discuss** puts the quoted question and a space there. When you
   send a prompt, at the terminal or through Remote Control (an `origin`
   whose `kind` is `composer` or `bridge`), the band's `prompt.submit` hook
   gives each quote that ends its line to one open question of the session
   with that text. It takes the next line that is not blank as the answer:
   the option with that text, or else a typed answer under the last letter.
   When open questions share the text, the quote goes to the one whose
   drawer last put that same answer in the prompt, or else to the oldest,
   so two questions never take one answer and a proposal you cleared from
   the prompt decides nothing. A quote with more on its line, as discuss
   leaves it, answers nothing, and so does any prompt you did not send,
   such as another session's message, a notification, or a scheduled
   prompt, which can quote a question its agent is still waiting on. When a
   subagent asked, the hook adds a note to the prompt's `context` naming
   it, so the main loop passes the answer on, as the loop skills already
   do. Once the prompt has entered, the band runs `project question
   answer`, `claim`, and `delivered`, because the prompt itself carried the
   answer to the main loop. **dismiss** runs `project question dismiss`,
   and a second press finds the question dismissed and does nothing.
3. **The band delivers every other recorded answer.** Whenever the store
   holds an answer in its session with no `deliveredAt`, given from a
   terminal or a dismissal, and the band is not already delivering it, the
   band runs `project question claim` and delivers the text it prints. It
   keeps the claim's token, and on each tick while the delivery is pending
   it renews the claim with `claim --renew`, so a submit waiting behind a
   long turn keeps the answer its own:
   - When the question has an `agentId` and `$.agent.list()` reports that
     subagent `pending`, `running`, `waiting`, or `idle`, the band sends
     the text with `$.session.send({ to: { agentId } })`. The list keeps an
     ended subagent too, as `completed`, `failed`, or `killed`, so the
     status decides, not the listing.
   - Otherwise, or when the send resolves `{ isDelivered: false }`, the
     band queues the text for the main loop with `$.prompt.submit`, naming
     the subagent when one asked, so the main loop relays it as the loop
     skills already do. The submit starts a turn once the session is idle.

   When the send resolves `{ isDelivered: true }` or the submit resolves
   without `drop`, the band runs `project question delivered` with the
   token. When the submit resolves `{ drop }` or either call rejects, it
   runs `project question reopen` with the token and raises a toast with
   the reason, and the question is open to answer again.

   The band keeps each delivery's token, route, and text in the session's
   `bandSending` state rather than in the module, because a hot reload
   drops what the module holds while a submit it made still waits in the
   session's queue. The band's own `prompt.submit` hook sees that submit
   arrive, finds its text in `bandSending`, and marks it delivered, so the
   submit settles whichever copy of the module is loaded, and a reloaded
   copy keeps renewing each submit it finds there. A send settles at once,
   so one that an earlier copy left is let go: its claim lapses, and the
   band delivers the answer again a minute later.
4. **Typed references get their context.** The `prompt.submit` hook the band
   already has tests your prompt for anything shaped like a reference. When
   one is, it runs `project question context` on the prompt and adds what
   it prints to the prompt's `context`, so the agent reads what `go with
   #337.3.b` means even when a subagent asked.

### 2.8 Files

| File | Change |
| --- | --- |
| `src/projector/questions.py` | New: the store and its lock, numbering and references, the checks, lettering, looking a reference up across sessions, claims and their expiry, finding references in text, the text of an answer, and `watch` |
| `src/projector/cli.py` | The `question` command and its subcommands, `--session`, and `PROJECTOR_SESSION` |
| `tests/test_questions.py` | New: each subcommand in a temporary state directory, each check, each reference form and non-form, a second answer, a reference in two sessions, two claims at once, an abandoned claim, retention, and `watch`'s lines |
| `docs/cli.md` | A section on asking and answering questions, and `65`, `66`, and `67` for the question commands |
| `hooks/questions.ts` | New: the quick test for reference-shaped text, the prompt's quoted question and answer and reading answers back from a sent prompt, the note naming a subagent, the route an answer takes, the store's directory and its signature, and the `ask` tool's description, schema, and arguments |
| `hooks/register.tsx` | Register `ask`; answer its `tool.call` through the CLI; read the store on the tick, shown or hidden; fill the prompt from a drawer, and record the answers a sent prompt gives; dismiss; claim and deliver every other recorded answer; attach typed references; draw the questions, their drawers, and the open count |
| `hooks/types.d.ts` | The `Question` and `Sending` types, and the band's `bandQuestions` snapshot and `bandSending` deliveries under the `projector` plugin's state |
| `hooks/questions.test.ts`, `hooks/band.test.tsx` | The route by the subagent's status, the list and the drawer, filling the prompt and answering from a sent one, each delivery and failure, the resolved line for a minute, and no hotkeys, answering `process.run` from memory |
| `docs/plugins.md` | A section on questions in Projector's band |

No skill changes in this project. A skill that asks through `project
question ask` needs the session's id in every host, which § 7 leaves open,
so the loop skills keep handing questions to the main loop until it is
settled.

## 3. Why this design

- **The CLI owns the questions.** Codex and Paseo need questions as much as
  Claude Code does, and the `project` CLI is what every host already runs.
  Keeping the store, the numbers, the checks, and the references in one
  place means each host's view draws and delivers, and none decides what a
  question is.
- **Files per session, under Projector's state directory.** A question
  belongs to the agents of one session, so each session has a directory of
  its own, and no view shows another session's questions. Files outlast a
  hot reload, a resume, and a restarted runner, where state kept in a
  process does not. The lock and the rename keep two writers, such as a
  band and a `watch`, from losing each other's change. The lock is the
  operating system's, so it ends with its holder: a lock directory that a
  crashed command left behind would need an age at which it counts as
  stale, and two waiters that judged that age at once could both take it.
- **Asking without waiting.** `AskUserQuestion` waits for an answer in a
  dialog. A loop must keep fixing and reviewing what the question does not
  block, so `ask` prints the reference and returns.
- **A recommendation with every question, always `a`.** You settle most
  questions by accepting the agent's answer, and an agent that must name
  one has thought the decision through. Making it `a`, the band's first
  option, gives the common answer one place everywhere.
- **References with one fixed form.** You can name a question or an answer
  from a terminal or in a prompt, and the same reference appears in the
  delivered text. A bare `2` would match "rerun the 2 failing tests", so a
  question without a pull request is `q2`, and a reference fixed when the
  agent asks cannot change when a view's labels do. The band shows none:
  its list reads more easily as plain questions, and the quoted question
  it puts in the prompt names one as surely, with words the agent reads.
- **Record, then claim, then deliver.** An answer can come from a terminal
  or from a view, and the agent can be in any host, so every answer takes
  one path: the store records it, one deliverer claims it under the lock,
  and the deliverer marks it delivered or reopens it. The claim keeps the
  band and a `watch` from both sending one answer. Renewing it while a
  delivery waits, and letting it expire only once renewals stop, keeps a
  slow delivery from being sent twice while a stopped deliverer still
  strands nothing. Claude Code's band is that deliverer in Claude Code
  because it runs in every session there; `watch` is the deliverer anywhere
  else, in the shape Projector's loops already use to hear GitHub.
- **The answer travels in your prompt.** An option fills the prompt rather
  than answering at once, so you can read the answer in context, add to
  it, or answer several questions together before anything reaches the
  agent. The quoted question and the answer on its own line are what the
  agent needs, and the same text tells the band which question you
  answered, so sending the prompt both delivers the answer and records it,
  with no second message to the agent.
- **One line each, in a list of their own.** One line each keeps the band
  short, and the questions read as one list below the pull requests:
  placed among the pull request lines they were hard to pick out. An open
  question takes the band to itself so its drawer reads without the rest.
- **A Button for the question, links for the options.** A question's line
  is a plain `Button`, so a press anywhere on it opens the drawer, on the
  main screen too. The options are `Markdown` links like the pull request
  numbers, so they read as links without an underline drawn by hand.

## 4. Acceptance criteria

| Do | Expect |
| --- | --- |
| `project question ask "Decline the finding?" --recommend "Decline it" --option "Add a budget" --pr owner/repo#337 --session s1` in a checkout of `owner/repo`, the third such question in `s1` | Prints `#337.3`; the session's directory holds it with `a` the recommendation and `b` the option |
| The same with no `--pr` | Prints the next `q` count, such as `q1` |
| The same about #12 in another repository | Prints `owner/repo#12.1` |
| A question of two lines or 121 characters, an empty recommendation, six other options, or an option equal to the recommendation | Exits `65` with the reason, and writes nothing |
| `project question answer '#337.3.a' --session s1` | Records `a`; a second answer exits `65`; an unknown reference exits `66` |
| `project question answer '#337.3.a'` with no session, when only `s1` has `#337.3` | Records `a` in `s1`; when two sessions have `#337.3`, exits `67` naming both |
| Two `claim` commands on one answered question at once | One prints a token and the text, the other exits `65`; a claim not renewed for over a minute lets a new `claim` take it, and the old token then exits `65` |
| In Claude Code, answer a question whose subagent has ended while the main loop runs a five-minute turn | The band's submit waits for the turn, renewing its claim on each tick; no tick and no `watch` claims the answer again; the main loop receives it once |
| `project question answer '#337.3.c' --text "keep the cap" --session s1` | Records a typed answer under the last letter |
| `project question watch --session s1` with one undelivered answer | Claims it, prints its `ANSWER` line once, marks it delivered, and prints nothing more for it |
| Two `answer` commands on one question at once | One records, the other exits `65`, and the file is whole |
| `printf 'Go with #337.3.b.' \| project question context --session s1` | Prints #337.3 and its choice `b`; `rerun the 2 failing tests` prints nothing |
| In Claude Code, a subagent calls `mcp__projector__ask` about #337 | The call returns `#337.3`; the band lists it below the pull requests, naming `#337`, and counts it |
| The CLI exits `65` for the tool's call | The call returns `{ deny }` with the reason |
| The installed `project` has no `question` command, so it exits `2` | The call returns `{ deny }` with the error and `run project upgrade` |
| An agent runs `project question ask` from the shell while the band is hidden | The band appears with the question within one tick |
| Open a question's drawer | The question draws inverted and has the band to itself; closing the drawer brings back the other questions and the pull requests |
| Click option `1.` in the drawer | The prompt holds the quoted question, a blank line, and the option's text; the store records nothing |
| Send that prompt, for a question a subagent asked | The store records `a` and marks it delivered; the main loop reads the prompt with a note naming the subagent; nothing else is sent; the line stays dim for a minute; a `watch` running for the session prints nothing for it |
| Click **discuss**, then send the quoted question with text after it on its line | The prompt reaches the agent, and the question stays open |
| Answer from a terminal after the subagent has ended | The main loop receives the text through `$.prompt.submit`, naming the subagent |
| `$.prompt.submit` resolves `{ drop }` | The band runs `reopen`, a toast gives the reason, and the question is open again |
| Answer `#337.3.a` from a terminal while the asking Claude Code session runs, its band hidden | Within one tick, the band claims and delivers it to the agent, and the store records it delivered |
| Type `go with #337.3.b` in the prompt | The prompt reaches the agent with #337.3 and choice `b` in its `context` |
| With a question's drawer open, type `2 things first:` in the empty prompt | The question stays unanswered |
| On the main screen, press ctrl+x tab and Enter on a question | Its drawer opens, and typing reaches the `Type something` field; an option's number, a `Markdown` link, takes no press there |
| No questions | The band and its divider look as they do today, and the tick starts no process |

`tests/test_questions.py` covers the CLI rows. `hooks/questions.test.ts`
and `hooks/band.test.tsx` cover the band rows but the main-screen one,
answering `process.run`, `agent.list`, `session.send`, and `prompt.submit`
from memory. Check the main-screen row by hand under tmux.

## 5. Cost

- **The CLI.** Each command reads one session's directory, a few small
  files, and writes one under a lock. `watch` reads the directory once an
  interval, 5 seconds by default.
- **The band.** Its tick lists one directory, shown or hidden, which starts
  no process, and starts `project question list` only when it changed,
  which is when a question is asked, answered, claimed, or delivered, and
  once more each tick while that read fails. Each
  answer then starts a process to record it, one to claim it, and one to
  mark it delivered, and one more each 5 seconds to renew the claim while a
  submit waits for the main loop; a renewal starts no `list`.
- **Prompts.** The `prompt.submit` hook already runs on every prompt.
  Finding the answers a prompt gives reads the band's own list of
  questions, so a prompt that answers none starts no process. It starts
  `project question context` only for a prompt with reference-shaped text.
- **The tool's schema.** With `isDeferred: false`, the schema rides in
  every request: a name, a short paragraph, and five properties.

## 6. Rollout

1. Build the CLI, its tests, and its section of `docs/cli.md` in one pull
   request. It is usable alone: an agent in any host can ask from the shell
   and hear answers through `watch`.
2. Build Claude Code's band and tool in a second pull request stacked on
   the first, since they run the commands the first adds.

## 7. Open questions

- **A session's id outside Claude Code.** Claude Code gives a session's id
  to a tool and to a skill. A Codex agent or a Paseo runner needs to pass
  its own to `--session` or `PROJECTOR_SESSION`, and which id each host
  offers is not settled. Until it is, an agent there passes an id its runner
  chooses. Owner: ninjudd, with Jason Fieldman for Paseo.
- **The loop skills asking through the CLI.** Once every host can name its
  session, `fix-pr` and the loops can ask through `project question ask`
  instead of handing questions to the main loop. Deferred until the
  question above is settled.
- **Opening a pull request's drawer on the main screen.** Its number is a
  `Markdown` link, which takes no press there. Switching pull request lines
  to `Button`s, as questions use, would fix it but loses the ctrl-click that
  opens the Projector summary. Owner: ninjudd, after trying question lines
  on the main screen.
- **Views for Codex and Paseo.** Codex has no band, and Paseo's clients
  could list and answer questions through `project question list --json`
  and `answer`. Deferred until the CLI ships.
- **Old session directories.** A session's directory stays after the
  session ends. Removing directories older than some age is deferred until
  they turn out to pile up.

## 8. Background

Jason Fieldman's [docket](https://github.com/jmfieldman/docket) prompted
this design, alongside a pain point of the owner's own: questions from a
loop lost in its transcript. Docket resolves a pull request's review
findings rather than an agent's questions, but its shape suggested several
of the decisions above: one CLI as the only writer, with every view going
through it; state kept in files written under a lock and renamed into
place; an answer that reaches the agent by starting a new turn rather than
by the agent polling; the recommended option always lettered first; and a
Paseo plugin that runs the same actions in Paseo's daemon. Projector's
version differs where its hosts and loops do: it serves Codex as well as
Claude Code, it delivers through a watcher as its loops already do, and it
raises a notification when a question arrives.

## 9. Outcome

The project shipped in two pull requests, #271 for the CLI and #273 for the
band and the `ask` tool, both released in 0.7.14. It continues as the
[inbox](../inbox/readme.md) project, which renames `project question` to
`project inbox`, so each command this plan names now runs under
`project inbox`. The inbox plan's § 7 carries every open question in § 7
here.
