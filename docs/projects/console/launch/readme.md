---
status: in-progress
priority: now
---

# Run agents from the Projector console

`projector` lets you launch and run Claude and Codex from the terminal,
while keeping the console useful for any process that uses Projector.

## 1. Problem

The console lists live sessions, pull requests, and inbox items. You can
answer or discuss an item, but you need the agent's own interface to start
work and manage its conversation. Registered external processes already
participate in the inbox; registration does not give the console control
of their execution.

The missing piece is control of agent execution. Projector already stores
questions, requests, answers, messages, and review events. Adding another
communication system would give a session two places to look for its work.

## 2. Solution

Keep Projector's communication layer and add Python workers that run native
agent loops. The watch daemon coordinates the workers; each worker owns
one agent connection and survives the console and daemon. The console
becomes the place to launch, converse with, and supervise those sessions.
External processes retain their existing inbox and messaging experience.

### 2.1 Launching and running a session

1. Run `projector` to open the console. Keep `project console` as a
   compatibility entry point. `project` remains the agent-facing CLI;
   `projector` is the human-facing interface.
2. Choose **New session**, then Claude or Codex, a checkout, an optional
   name, a model, and a multiline prompt. Identify native settings as the
   source of permission policy before launch, including automatic approvals.
   Show the effective policy when the runtime reports it. Claude reports
   its mode when the first turn initializes; it has no preflight policy
   query in the tested SDK. Launching adds no permission bypass.
3. The daemon records the launch and starts a detached worker in the
   optional agent environment. The worker opens the runtime, records its
   native resume ID, and registers with Projector. Normalize hook and
   worker registration to one session identity and one console row.
4. The worker delivers the prompt and streams text, tool activity, usage
   when available, and turn outcomes. The conversation remains readable
   after a turn or worker ends. The home list retains stopped managed
   sessions so you can resume them; existing external-session visibility
   and retention rules remain intact.
5. Type a follow-up in the conversation. While a turn runs, follow-ups
   queue in order and show their delivery state. You can edit or remove
   unsent follow-ups. **Interrupt** ends the active turn without dropping
   queued work; require an explicit send to run it afterward. **Stop**
   closes the worker and leaves history and pending input available.
6. Questions, requests, review events, agent messages, and your answers
   travel through the existing inbox and watch delivery. A native approval
   or question becomes an inbox item linked to its pending runtime request.
   Your answer resumes that request, rather than starting an unrelated turn.

Use the official Python Claude Agent SDK's long-lived client for Claude,
and a Python asynchronous JSON-RPC client for `codex app-server`. Port the
useful Claude and Codex adapter contracts from `ninjudd/agent-sdk`; a port
of its other backends is outside this project. Native events determine turn
boundaries. A quiet transcript, a tool finishing, or a process going idle
does not prove that a turn or a larger task succeeded.

### 2.2 Opening a native session and returning

**Open in Claude** or **Open in Codex** is a controlled handoff. Quiesce the
worker, resolve or cancel pending native requests, release the SDK connection
and delivery ownership, then open the native CLI at the saved resume ID.
Do not describe this as attaching to a live SDK terminal. Use a tmux pane
when available, or suspend curses and restore it when the CLI exits.

During handoff, keep new input durable and suspend managed submission.
Keep Projector inbox answers, messages, and review events queued while the
native terminal owns the session. Fence the native panel and Codex queue
throughout handoff so they cannot compete with the managed delivery ledger. Returning to managed control closes that native
controller, reacquires ownership, resumes the same history, and reconciles
pending deliveries. If exclusive control cannot be established, remain
handed off rather than starting a second controller.

Prove this round trip separately for Claude and Codex before exposing it.
Claude SDK sessions need an explicit resume ID rather than the native
session picker. Repository instructions, skills, plugins, MCP configuration,
and Projector actions must work on both sides. SDK-only in-process tools
cannot silently disappear during handoff; use portable Projector CLI actions
where possible and report unsupported configuration before launch.

### 2.3 The launch and runtime records

The watch daemon discovers workers and routes existing events; it does not
hold SDK connections. Its version replacement and five-minute idle shutdown
must not terminate agents. Workers renew their own presence, reconnect to a
new daemon, and retain pending work while it is unavailable. A coordinator
restart can recover a launch in progress without launching it twice.

Store runtime metadata beside the session's existing local watch state,
using its lock and atomic-write conventions. Keep pre-registration launch
records in a separate local launch directory until the native ID is known.
These are execution records, not a second agent-message mailbox.

| Record | Required content |
| --- | --- |
| Launch | Launch ID, checkout, backend, requested settings, creation time, and worker identity; links to the registered session when known |
| Runtime | Schema version, Projector session ID, native resume ID, backend and version, worker incarnation, ownership lease, capabilities, lifecycle state, and last error |
| Command | Stable command ID, source reference and claim, runtime generation, input, queued/submitting/accepted/finished/uncertain state, native turn ID when available, and outcome |
| Event | Increasing local sequence, session and runtime generation, command and native turn IDs when available, event kind, timestamp, and payload |
| Pending request | Native request ID, runtime generation, inbox reference, allowed responses, and whether the request still accepts an answer |

A worker restart keeps the session ID but acquires a new runtime generation.
Reject late callbacks and answers to obsolete requests. Expired approvals
never authorize a later tool call. Separate native prompts that cannot be
resumed after a crash from durable Projector questions that still need you.

Persist lifecycle transitions and completed results before acknowledging
consumption. Coalesce text deltas for storage and redraw; retain tool IDs
and outcomes so reconnecting does not duplicate a tool or final answer.
Native transcripts remain authoritative for native resume. The console's
normalized event log supplies incremental display and command correlation.

### 2.4 Changes by component

| Component | Change |
| --- | --- |
| `src/projector/console.py` and new console entry module | Launch form, conversation composer, stream display, lifecycle controls, native handoff, and capability-driven external rows |
| New runtime and worker modules under `src/projector/` | Durable execution records, single-owner lifecycle, Claude SDK adapter, and Codex app-server adapter |
| `src/projector/watch.py` and `src/projector/codex_watch.py` | Worker discovery and managed delivery; exclude competing native delivery without changing external behavior |
| `src/projector/agents.py` and `src/projector/inbox.py` | Reuse existing identity and delivery; preserve public message and inbox semantics |
| `pyproject.toml`, `install.sh`, and upgrade paths | Install `projector` beside `project`; offer a versioned optional agent environment without runtime dependencies in the core CLI |
| `src/projector/permissions.py` and host packaging | Give the new human entry point the console's permission protection; verify Projector actions load in SDK sessions without requiring a UI hook engine |
| Runtime integration tests and existing console, watch, inbox, agent, and packaging tests | Exercise failures and compatibility with fake runtimes; record separate real-runtime probes |
| `docs/cli.md` and installation documentation | Describe the new command, optional dependencies, supported runtime versions, auth, recovery, and native handoff when those features land |

### 2.5 One communication and delivery path

Reuse `agents.py`, `inbox.py`, and the watch event queue. Conversation input
uses the existing person-to-session message path. A managed delivery adapter
adds runtime submission and acknowledgment to that path. It also consumes
existing review and agent-message events, preserving their identifiers,
thread references, ordering rules, and trust labels.

The worker serializes input for its session. Persist a command ID and its
source event or inbox claim before submission, then record native acceptance
and the eventual outcome. Repeated observations of the same accepted command
do not create another turn. Renew inbox claims while delivery is pending;
settle them only on confirmed acceptance. Delivery acceptance and turn
success are separate records. Never use delivery failure settlement to
erase input whose acceptance is uncertain.

A runtime ownership record selects exactly one deliverer. While a managed
worker owns a session, neither the Claude panel nor `codex queue` delivers
its events. Watcher upgrades, stale leases, and concurrent consoles cannot
start a second worker or controller. Native takeover changes that ownership
explicitly. Keep the existing delivery adapters for external sessions.

On restart, reconcile commands against runtime state and persisted native
history. Retry commands known not to have been accepted. If acceptance
cannot be established, show **Needs recovery** and retain the command for
your decision. Do not automatically replay work that may have changed files
or called external tools. Worker crashes are visible terminal failures;
automatic task reruns and a team scheduler are outside the first version.

### 2.6 Any process that uses Projector

Discover sessions through Projector registration and supported delivery
capabilities, rather than a hard-coded list of two hosts. An external
process can register, exchange agent messages, and post inbox items without
becoming a managed agent. Show whether it has a working answer-delivery
route. Registration alone does not grant chat, interrupt, stop, or resume.

The console renders supported controls from capabilities. Existing Claude
and Codex sessions keep their current delivery and inbox controls.
Generic processes keep their inbox and messaging features even without an
agent transcript. The console never infers control from a PID or kills a
process it did not launch. Peer messages retain their existing lack of user
authority; a message cannot grant a native permission or answer as you.

## 3. Why this design

- **Python fits the coordinator.** The console, durable state, and daemon
  already use Python. A small adapter port avoids a second language runtime
  solely for a wrapper. Claude's dependency stays in an optional worker
  environment; normal `project` commands require only Python 3.11 or newer.
- **Workers own execution.** The watcher restarts during upgrades and exits
  when idle. Keeping SDK clients in workers makes those events independent
  of agent lifetime and lets several consoles observe one session.
- **Projector owns communication.** Its inbox already handles questions,
  requests, discussion, claims, and delivery. Its agent messages already
  have IDs, reply threads, and local and remote routes. The comparison in
  [Communication references](communication.md) identifies the smaller gaps.
- **Structured runtimes expose turns.** A PTY and screen parser add turn
  detection and permission-screen handling that the SDK protocols provide
  directly. Trip and Tripping remain references rather than dependencies.
- **The human command stays separate.** A blanket permission for `project`
  must not let an agent start agents under your credentials or supply your
  approval. The new entry point retains the console's terminal requirement
  and explicit host permission rules.

## 4. Acceptance criteria

| Do | Expect; failure this catches |
| --- | --- |
| Install the core package without agent extras; run both console entry points | Both open the existing console; ordinary `project` commands import no SDK. A missing optional dependency offers setup instructions and leaves no orphan worker |
| Launch Claude and Codex in a temporary checkout with quotes, newlines, and leading dashes in the prompt | The intended checkout, settings, and exact prompt reach one native session, with one Projector identity; catches shell expansion and duplicate registration |
| Stream tools and multiple turns, then queue two follow-ups during a turn | Output remains ordered and responsive; each input reaches one turn in order. An interrupted or failed turn is not marked successful |
| Answer a Projector question, a native approval, and a native question while two consoles are open | The correct pending operation resumes once; stale answers and agent-authored permission grants are refused; both consoles reflect the result |
| Deliver review events and agent replies while a managed turn runs | The existing event identifiers and trust labels survive; the worker serializes delivery and the native queue or panel does not deliver a duplicate |
| Close the console, restart or upgrade the watcher, and reopen it | The same worker and native session continue; history and queued input recover. No new prompt or worker appears |
| Crash at the submission and acknowledgment boundaries, then restart | Proven unaccepted input can retry; uncertain acceptance needs recovery. Previously accepted tools and prompts do not rerun automatically |
| Kill a worker with queued work and a pending approval | Failure is visible, queued work remains inspectable, and an obsolete approval cannot authorize the resumed runtime |
| Use native handoff for each backend, add a native turn, then return | The same conversation includes the native turn; queued inbox answers survive and only one controller delivers at a time |
| Register a generic process and use existing external Claude and Codex sessions | Inbox, replies, and review subscriptions still work; unsupported controls are absent and the process is never terminated by the console |
| Replay events, include an unknown event kind, or truncate the final log write | Completed results appear once; unknown events do not stop later delivery, and damaged state is reported without discarding unrelated pending work |

Use fake SDK and app-server peers to inject crashes at these boundaries.
Use temporary repositories for checkout and mutation tests. Run real Claude
and Codex probes for authentication, plugin loading, approvals, turn events,
and the native round trip; record runtime versions and observed limitations.

## 5. Cost

The baseline is one lightweight coordinator plus one worker and native runtime
per managed session. Measure idle CPU, memory per session, and transcript
storage with ten idle sessions and two streams. Batch deltas and incrementally
read events; do not scan complete transcripts or rewrite full conversations
on each redraw. Slow GitHub polling must not block input or output. Choose
log retention limits from the measurement before shipping, and preserve
unsettled commands and pending inbox delivery regardless of display retention.

## 6. Open questions

- **Runtime and installation decisions.** Resolved by the probes and
  implementation recorded in § 9. The tested native versions are the minimum
  supported versions. Launch rejects older or unrecognized version output.
  The optional environment pins the SDK and source snapshot; the core CLI
  retains no runtime dependencies.
- **Permission preview.** The tested Claude SDK reports its effective mode
  at first-turn initialization. Preflight display of the fully resolved
  policy is deliberately deferred. The launch form identifies native
  settings and configured automatic approvals, and the conversation shows
  the reported policy. Projector adds no bypass or permission-setting UI.
- **Deferred features.** A team scheduler, typed delegated task/result APIs,
  autonomous task retries, additional backends, network session hosting, and
  Windows console support are separate work. The phone project remains
  separate. Opening a terminal over SSH does not require a network API.

## 7. Background

### 7.1 The Codex launch test

The earlier CLI probe established that `codex exec` could create a thread and
`codex queue --resume` could deliver a later turn. It proves an external
session route, not exclusive ownership or a native app-server round trip.
Retain that external route while managed sessions use the structured runtime.

### 7.2 Claude Code's background sessions

Claude's native background sessions are an alternative execution mechanism.
They do not prove that an SDK-launched session has a terminal to attach to.
The managed design requires saved-session resume and explicit handoff.

### 7.3 Ending options before the prompt

The CLI probes found prompts that native parsers could mistake for options
or subcommands. Keep launch input separate from executable arguments. The
SDK and JSON-RPC paths carry prompts as data; native handoff commands use
argument lists and saved IDs rather than shell interpolation.

### 7.4 SDK and authentication references

The [agent-sdk adapters](https://github.com/ninjudd/agent-sdk/tree/main/src/backends)
provide a reusable contract for Claude's SDK and Codex's app-server.
The [Claude Python SDK reference](https://code.claude.com/docs/en/agent-sdk/python)
describes the long-lived client. Claude documents explicit resume for SDK
sessions in its [session guide](https://code.claude.com/docs/en/sessions).

As reviewed on 2026-11-25, Anthropic's
[SDK subscription guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
says the planned billing change is paused. That billing statement does not
replace the [rules for third-party authentication](https://code.claude.com/docs/en/legal-and-compliance).
Check both during the runtime probe; avoid building a Projector login flow.

## 8. Rollout

1. Prove both runtime adapters and native handoff in an isolated prototype.
   Record results for the blocking questions, then move the plan to `ready`.
2. Add the optional worker environment, execution records, ownership, and
   managed delivery. Prove restart and failure behavior before connecting
   console controls to a real agent.
3. Add the `projector` entry point and managed conversation controls; verify
   existing external sessions in the same console throughout the rollout.
4. Complete real-runtime acceptance checks and installation documentation.
   Mark this project completed with the final implementation change.

## 9. Implementation decisions and verification

The implementation uses Python Claude Agent SDK 0.2.189 with Claude Code
2.1.332, and the stdio app-server protocol with Codex CLI 0.172.0. Both
adapters completed multiple turns using native sign-in. Projector handles
no provider OAuth tokens. The paused Claude SDK billing guidance was checked
against Anthropic's subscription and authentication documentation.

Native terminal probes resumed each SDK-created session, added a turn in the
native UI, and resumed that history through the adapter. Codex required
`--no-daemon`: its default shared daemon retained an active writer after the
native UI exited. Projector therefore uses foreground native resume and an
inherited file-descriptor lease. Ctrl-C stays with the native CLI; closing
or killing a wrapper cannot grant a second controller access to a live child.

Projector communication remains queued throughout native handoff. This
replaces the planned native-deliverer transition in § 2.2 with a simpler
single delivery owner. Portable Projector CLI actions work in SDK sessions;
the bridge requires no UI hook engine or in-process Projector tool server.
Native runtime settings continue to load repository instructions and native
configuration. The console follows the session-column layout merged during
implementation; external sessions use that layout's inbox and message panel.

Native question and permission probes passed for both backends. Claude's
`AskUserQuestion` and Codex's plan-mode `requestUserInput` resumed their
waiting tools with the chosen answer and note. Protected-tool probes denied
the requested operation and left the output file absent. Approval notes deny
the original operation and preserve the note for the agent. Native requests
expire on interruption or restart; their IDs, generation, and inbox references
remain tied to the live callback. Pending native requests offer a typed
clarification instead of discussion or snoozing that their waiting tool could
not consume. Ordinary Projector inbox items retain those controls.

The optional environment installed successfully. A separately built core
wheel installed without SDK dependencies and exposed both entry points.
A real 120-column terminal check exercised a typed inbox answer, cancelled
a launch form, and exited cleanly. Automated tests cover immutable source
snapshots, stopped-session environment retention, old-runtime rejection,
queued input edits, native delivery fencing, stale requests, uncertain
acceptance, malformed protocol output, and damaged state preservation.
A mutation pass makes each new behavior test fail on its named regression.

Ten real workers (two Claude and eight Codex) completed an initial turn.
After the isolated watcher stopped, two concurrent streams completed in
10.2 seconds with every worker PID and native session unchanged. All ten
workers then stopped and resumed without replaying a command. Final
steady-idle measurements, including configured native child processes,
were 430 MiB per Claude session and 410 MiB per Codex session; the Python
workers accounted for 89 MiB and 51 MiB respectively. Each Python worker
used about 0.9% of one CPU core while idle. These are local measurements,
not resource requirements for other native configurations.

The cost pass then removed inbox scans and acceptance writes from each
text-delta event. Input polling is bounded to twice a second and controls
to five times a second. Continuous output flushes every quarter second or
16 KiB, including streams with no pause. Console rendering reads cached
runtime data; incremental reads and bounded display retention remain separate
from pending command and inbox records. The ten-session and two-stream
checks passed again after those changes.

### 9.1 Acceptance evidence

| Criterion in § 4 | Evidence |
| --- | --- |
| Dependency-free core and both entry points | Isolated wheel installation, callable entry-point packaging checks, core import without the SDK, and the installed console's real terminal check |
| Exact launch input and one identity | Console launch and worker multiline-input tests; live Claude and Codex worker registrations and native session records |
| Streaming, tools, queued follow-ups, and outcomes | Two-turn adapter probes, native question and tool probes, queue and interruption tests, continuous-stream flush test, and two-stream measurement |
| Existing questions and native callbacks | Existing inbox tests; typed-answer console test; live question and denial probes for each runtime; pending-request expiration and callback-generation tests |
| Review and agent-message delivery | Existing Codex watch queue tests, managed delivery-fencing test, and worker ingestion through that same queue and eligibility checks |
| Console and watcher independence | Detached-worker smoke probes; ten-session restart measurement; stopped-session and saved-source tests |
| Ambiguous submission and accepted input | Store recovery, explicit confirmation, stale-generation, and single-terminal-result tests; no-replay check across ten real resumes |
| Worker failure with queued input or an approval | Restart-withdrawal test, failure-preservation test, interrupted queue test, and damaged-state preservation test |
| Native handoff and return | Separate Claude and Codex native UI round trips, idle-handoff test, native-lease refusal test, and inherited-lease process test |
| Generic and external sessions | Existing console/watch suites and generic-process compatibility test; no managed lifecycle controls on external rows |
| Unknown events, duplicate output, and damaged state | Incremental cursor test, native protocol subprocess tests, duplicate-result test, display-retention test, and invalid or empty database-header cases |

### 9.2 Deliberate limits

Preflight display of Claude's fully resolved permissions remains deferred as
§ 6 describes. Pending native requests use answer or cancellation rather than
ordinary discussion, and Projector delivery pauses during native handoff.
Codex form elicitation is bridged; URL elicitation and Claude MCP elicitation
are not bridged in this version. Complete those interactive server setup
steps in the native runtime. Native MCP configuration and tool calls remain
with their native runtime. A team scheduler, typed delegated task results,
automatic reruns, additional backends, and Windows console support remain
outside this project's outcome.
