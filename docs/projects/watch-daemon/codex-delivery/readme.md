---
status: in-progress
priority: now
---

# Deliver watch events to Codex sessions

Projector wakes a subscribed Codex session when review or fix work arrives,
while Claude Code continues to use its existing mod.

## 1. Problem

Projector's watch daemon polls GitHub once for the pull requests that live
sessions fix or review. It writes events to each owning session. Claude
Code's mod renews that session and submits the events as prompts.

Codex has no delivery adapter. The shared skills tell it to run
`project watch stream` under a persistent process or monitor facility, but
printing a line does not establish that an idle Codex session receives a
new turn. Without renewal, the daemon drops the session after five minutes.
The review and fix loops therefore depend on host behavior Projector does
not provide or verify.

## 2. Solution

Add a Codex adapter to the existing daemon. Trusted plugin hooks register
the session and observe delivery. The daemon sends its existing event lines
through `codex queue --thread <id> --message <batch> --resume`. Keep Claude's
mod, the stream deliverer, event routing, and the shared skill tree. Codex
needs no model turn when no event arrives.

### 2.1 Runtime flow

1. A Codex `SessionStart` hook invokes `project watch codex-hook`, with the
   host's JSON input on standard input. The adapter uses `session_id` as
   the watch session and queue target. It never selects a thread by title,
   scans transcript files, or invents an ID. Return brief additional context
   telling the conversation to pass this ID to watch commands and workers.
   Codex reports the parent session ID for subagent hooks; workers keep that
   subscription owner rather than register separate delivery targets.
2. Registration records the resolved absolute `codex` executable and the
   hook's `cwd`. It adds no subscription and starts no daemon until this
   session has subscribed work. The existing skills still authorize each
   review or fix subscription. `PostToolUse` for `Bash` passes successful
   command output to the existing `add_from_output` parser, as Claude's mod
   does. Honor `watch.auto_start: false` and exclusions. Observe completed
   unified-exec commands, including completion reported by `write_stdin`.
3. A Codex registration remains live for 24 hours after host activity when
   it has subscriptions or pending events, even while the conversation is
   idle. `SessionStart`, `UserPromptSubmit`, `PostToolUse`, and `Stop` renew
   it. Delivery attempts never renew it. A `SessionEnd` hook disconnects it;
   expiry bounds polling after a crash that produces no end hook. Use
   wall-clock expiry across daemon restarts; sleep forgiveness must not
   extend this lease. Existing mod and stream renewals retain their
   five-minute lapse rules. Each hook with subscribed or pending work runs
   `ensure_daemon`. If the daemon exits while Codex is idle, it stays down
   until the next hook or `project watch` command starts it. To restart it
   without a new conversation turn, run `project watch status`. Persisted
   batches recover on that restart. Automatic recovery without host
   activity or a watch command is deferred; the lease alone cannot restart
   a process, and events are not detected while the daemon is down.
4. On each daemon tick, claim up to 8 KiB of complete pending event lines
   under the session lock. Send at most one unconsumed batch per session.
   Preserve order and coalesce by work identity across the pending batch
   and Codex's queued events. The producer attaches a stable key before
   rendering the line: repository, PR, role, and the standing item's key.
   A review head uses its SHA, model, and reviewer; a finding uses its
   thread ID, head SHA, and latest comment ID and body digest. Other fix
   items reuse `route_fix`'s standing key, plus a body digest for review or
   verdict text. Reminder wording, including `(still open)`, never changes
   the key. A stalled-review restart includes the stalled lock's identity;
   `RESPONDED` includes the existing head/answer flag. Both use distinct
   event kinds so they cannot disappear behind a head announcement. Events
   without a standing key use their kind and a digest of their full line.
   Keep the first line for a key already queued or pending; preserve new
   revisions as distinct items. Reminders create neither stored copies nor
   additional queued prompts while a batch awaits consumption. After its
   acknowledgment, a later reminder can request work that still stands.
   Terminal events such as `MERGED` and `CLOSED` remain deliverable after
   the last subscription ends.
5. Run queue commands as argument arrays, outside the session lock and the
   GitHub polling path. Use a bounded pool of four workers, a 15-second
   process timeout, and one command per session at a time. Run from the
   registered directory. A missing directory or executable reports blocked
   delivery instead of selecting another checkout or executable.
6. Prefix the message with `PROJECTOR WATCH <batch-id>`, followed by the
   existing event lines and the owning session ID. `UserPromptSubmit`
   acknowledges only a marker that matches this session's pending batch.
   Retain a receipt for 24 hours; reject a repeated consumed marker before
   it starts another model turn. After acknowledgment, discard that batch
   and allow the next one. The conversation routes the events to the
   existing per-PR review and fix workers. Delivery does not publish
   reviews, alter draft state, or merge.
7. Treat exit zero as queued, including when stderr reports a failed resume.
   Keep the batch until acknowledgment, and expose any warning. Never
   resend a queued batch merely because it has not run. Retry definite
   queue failures after 30, 60, 120, then 300 seconds, capped at 300 seconds.
   A timeout or crash during submission has an uncertain outcome: retain
   the batch and stop automatic retries. The command
   `project watch codex-retry --session <id>` resends that batch and reports
   that it can duplicate an accepted message. A late acknowledgment remains
   valid.
8. `project watch disconnect --session <id>` disables Codex delivery and
   cancels unsent work without deleting subscriptions or pending events.
   The skills use existing `remove` and `drop` commands when you ask to stop
   work on a PR. Before submission, recheck registration and subscriptions;
   filter work for removed or excluded targets, preserving terminal notices.
   A message already accepted by Codex cannot be recalled. The receiving
   workflow rechecks subscriptions and current PR state before acting.

Start with local Codex sessions served by the local daemon and CLI 0.166.0
or newer. Check `queue --help` for the required options at registration;
cache that result until the executable changes. Unsupported versions,
untrusted or disabled hooks, and sessions on another server use the existing
stream path with an explicit report that automatic Codex delivery is absent.
Do not claim that a background stream wakes an idle session. Remote server
selection, credentials, scheduled tasks, and an MCP service are deferred.

### 2.2 State and command contract

Keep additions under `sessions/<session>/`; accept all existing records.

| Record or output | Fields and behavior |
| --- | --- |
| `codex.json` | `schema_version: 1`, `thread_id`, `executable`, `cwd`, `expires_at`, and `enabled`. A hook-supplied ID binds the registration to this session. Store no token or remote address. |
| Capability and submission records | `codex.json` also caches `executable_signature`, `supported`, and `last_error`. `codex-submit.lock` is an OS lock held across the queue call, so recovery distinguishes a running submission from a crashed one. `codex-last.json` retains the consumed batch ID and its last delivery state. |
| `codex-events.json` | `schema_version: 1` and an ordered `items` array. Each item holds a stable `key` and unchanged event `line`. Write the file atomically under the session lock; skip keys already queued or pending. Mod and stream sessions retain the plain `events` file. |
| `codex-pending/<uuid>.json` | `batch_id`, claimed `items`, `state` (`prepared`, `submitting`, `queued`, `uncertain`, or `failed`), `attempts`, `next_attempt_at`, and bounded `last_error`. Claim under the lock: write the pending record before removing its keys from `codex-events.json`. Before another append or dispatch, recover an interrupted claim by removing those keys from the queue. New revisions have different keys and survive. Persist `submitting` before the call; recover it as `uncertain`. |
| `codex-receipts/<uuid>.json` | `batch_id` and `consumed_at`. Write the receipt before removing its pending batch; recover a matching pending batch as consumed. Retain receipts for 24 hours and until any concurrent command result is settled, whichever is later. |
| Delivery status | Add per-session transport, expiry, batch ID, state, and diagnostic to `watch status --json` and readable status. Distinguish queued, consumed, failed, uncertain, disconnected, and expired delivery. Do not log event bodies or full prompts. |
| `watch add` result | `events: codex` means a supported, enabled, unexpired registration handles delivery. Preserve `events: mod`, `events: stream`, and `events: none` and their JSON equivalents. |
| `codex-hook`, `codex-retry`, and `disconnect` | The first consumes hook JSON; the latter two take `--session`. Validate all IDs and input. Malformed hook input neither registers nor queues anything. Hook failures report a bounded diagnostic without blocking the user's turn. |

Keep Claude's `session.json` contract intact. Resolve liveness through one
shared helper used by scanning, delivery selection, and status. A fresh mod
or stream registration and a Codex registration must not both consume the
same session's events. Refuse a conflicting registration with a diagnostic;
require disconnect before switching that session's transport.
When a disconnected Codex session switches to stream delivery, render its
unsent queued items as plain lines through the same claim and recovery
logic. Keep accepted or uncertain batches separate until acknowledged or
explicitly retried; switching transport must not resend them implicitly.

### 2.3 Components

| Component | Change |
| --- | --- |
| `src/projector/watch.py` | Extend liveness and delivery selection, schedule Codex delivery, and expose status. Carry work keys from the existing event producers to the destination adapter before rendering. Preserve mod and stream event text. Reuse subscriptions, ownership, reminders, and `add_from_output`. |
| New `src/projector/codex_watch.py` | Own hook input, registration, durable batches, acknowledgments, queue execution, and retries. Use Python's standard library. |
| `src/projector/cli.py` | Add the three commands, dispatch hooks before unconditional daemon startup, and report the additional transport and delivery states. |
| `.codex-plugin/plugin.json` and new `codex-hooks/hooks.json` | Explicitly point Codex at its command hooks. Invoke the installed `project` CLI; use documented plugin-root expansion if a bundled launcher is needed. |
| `hooks/hooks.json`, `hooks/register.tsx`, and `.claude-plugin/plugin.json` | Preserve Claude's module discovery and delivery behavior. Codex must never load the Claude TypeScript module. |
| `skills/review-pr/SKILL.md`, `skills/fix-pr/SKILL.md`, and `skills/review-repo/SKILL.md` | Teach the shared workflow the `events: codex` result, parent session identity, and delivery recovery. Retain mod and stream instructions. |
| `docs/cli.md` and `docs/plugins.md` | Document local setup, hook trust, the 24-hour idle limit, status, recovery, stopping, and fallback behavior on both hosts. |
| `tests/test_watch.py`, new `tests/test_codex_watch.py`, and `tests/test_packaging.py` | Exercise the behavior below with temporary state and fake executables; verify each host loads its own hook entry point. |

## 3. Why this design

The daemon already decides who owns each event and limits GitHub polling.
Adding delivery there preserves that work and the same review method on
both hosts. Keep batching and recovery in one adapter instead of repeating
them in the review and fix skills.

Codex's queue is a supported CLI interface. A separate SDK controller or
MCP server adds a runtime and an authentication surface for the same job.
Scheduled model checks spend turns when no work arrives. Async hooks cannot
start an idle turn, so use hooks for lifecycle and queue for wake-up.

Explicit Codex hook packaging avoids the default `hooks/hooks.json`, which
belongs to Claude's mod. This change replaces Codex's stream recommendation
only where queue delivery is available. Every other host keeps that fallback.
Leave the parent plan's numbered sections intact; this child specifies the
additional transport.

## 4. Acceptance criteria

| Exercise | Required result and bug caught |
| --- | --- |
| Subscribe Codex session A to review `acme/app#14`; advance the fixture from head `a` to `b`. | The fake CLI receives A's exact thread ID and one prompt naming head `b` and its review loop. Catches delivery to the wrong session or loss of the new head. |
| Emit a finding for a fix subscription through the same daemon. | The prompt preserves the finding and `fix-<number>` action. Catches an adapter that handles only review events. |
| Put Claude, Codex, and stream sessions in one temporary state root. | Each event reaches only its owning transport; GitHub query counts equal the existing daemon baseline. Catches double consumption and host-specific polling. |
| Queue a first head announcement and a finding, then emit their actual `(still open)` reminder variants while Codex is busy. | One copy of each work item remains across queue and batch; acknowledgment starts no duplicate prompt. A changed head or a new finding reply survives and dispatches next. Catches wording-based deduplication and lost concurrent appends. |
| Return exit zero plus a resume warning; restart the daemon before consumption. | The batch remains queued with its warning and is not resent. Catches the false assumption that queue success proves execution. |
| Fail the CLI, time out during submission, or interrupt each durable transition. | Definite failures back off; uncertain submission requires explicit retry; pending lines survive restart. An acknowledgment racing command completion cannot resurrect the batch; repeated consumed markers start no model turn. Catches loss and duplicate submission races. |
| End or expire a Codex registration, including across sleep and daemon restart. | Delivery stops; an idle subscribed registration remains live within its lease. Claude and stream lapse exactly as before. Catches immortal registrations and premature Codex expiry. |
| Stop the daemon while Codex is idle, then invoke a hook or `project watch status`. | No automatic restart occurs before that trigger; the command starts one daemon and recovers pending batches without resending an accepted batch. Catches a false recovery guarantee and lost restart state. |
| Drop a target while its batch waits; merge the last subscribed PR. | Dropped work does not dispatch; the terminal notice still does. Catches work after opt-out and lost cleanup events. |
| Feed successful push output through a child hook, with automatic subscription enabled, disabled, and excluded. | Only the authorized parent subscription is added. No child queue target appears. Catches authority expansion and worker-owned delivery. |
| Use spaces in executable and checkout paths, malformed JSON, an unsupported CLI, or conflicting transport registration. | Arguments remain intact; invalid input cannot route a prompt; fallback and diagnostics are truthful. Catches shell interpretation and accidental transport takeover. |

Use fake `gh` and `codex` executables at the process boundary, controlled
clocks, temporary Git repositories, and restartable state. Prove each new
regression test fails when its claimed fault is introduced. Run the full
repository validation gate from `AGENTS.md` before handover.

Also test both hosts against a disposable repository and PR. In Codex,
trust the hooks, subscribe, let the session become idle, and trigger a new
head and a finding. Observe automatic turns, hook acknowledgment, and reuse
of the PR's worker. Exercise an unloaded thread with a running local daemon.
In Claude Code, repeat review and fix events through the mod and observe
that no Codex process runs. These integration observations verify actual
host hook delivery; fake subprocess tests cannot establish it.

## 5. Cost

For an idle registered session, require zero queue subprocesses and zero
model turns. Preserve the existing conditional REST and GraphQL counts.
Each session adds one small registration read per daemon scan. Queue work
is bounded by four concurrent processes, one outstanding batch per session,
and the byte limit. Measure these bounds with the controlled-clock tests,
including a hung Codex process while another session receives an event.

Repeated reminders for an outstanding batch must not grow stored copies.
Keep distinct events durable; do not silently discard overflow. Expired
registrations use the existing seven-day session cleanup. No new runtime
dependency or service is required.

## 6. Background

- **Current delivery.** `watch.DELIVERERS` accepts `mod` and `stream`.
  `Daemon.scan` uses a five-minute lapse. `stream` renews every 30 seconds;
  Claude's `deliverEvents` in `hooks/register.tsx` submits prompts.
- **Codex queue.** [The CLI reference](https://learn.chatgpt.com/docs/cli/reference#codex-queue)
  documents thread IDs, local daemon requirements, and successful queueing
  despite resume failure. [The upstream change and integration coverage](https://github.com/openai/codex/pull/57232)
  cover unloaded threads and resumption failure. CLI 0.166.0 adds `--resume`.
- **Hook contracts.** [Codex hooks](https://learn.chatgpt.com/docs/hooks)
  document parent `session_id` values for subagents, prompt input,
  lifecycle events, hook trust, and the inability of async completion to
  start a turn. [Plugin packaging](https://developers.openai.com/plugins/build/plugins)
  permits an explicit `hooks` path in `.codex-plugin/plugin.json`.

## 7. Implementation and verification

The implementation adds the Codex adapter, command hooks, keyed producer
events, bounded dispatch, recovery, CLI status, and the shared workflow and
documentation changes. Claude's production hooks module is unchanged. Its
test mount supplies the current host's required `isCollapsed` prop, so
the hooks type-check against the generated declarations.

The focused tests exercise actual producer reminder variants, new heads
and replies, both roles, shared GitHub polling, transport isolation, batch
limits, acknowledgment races, dropped work, retry timing, lease expiry,
and a hung process beside another session. Native Codex `PostToolUse`
supplies plain text in the tested client; the adapter also accepts
structured completion results and filters authoring commands before
passing their output to the existing subscription parser.

The full Python gate passes 1,260 tests. Both plugin validations, plan
checks, and the hooks type-check pass. The current Claude test runner
reports 202 passing tests and 25 panel-rendering expectation failures;
an isolated copy of unchanged `main` has the same 25 failing test names. A live
Claude session received a notice through its mod while idle and replied.
A native Codex queue resumed an unloaded test thread and received its
reply; temporary startup-only hook overrides did not survive that resume,
so that run does not prove acknowledgment through installed hooks.

The combined live Codex check remains pending. Run
`PYTHONPATH=src python3 tests/codex_smoke.py --review-hooks` with native
Codex and Node 22 or newer. The test creates a separate loopback server,
reviews four temporary hooks in the native hook browser, restarts the server
to unload the thread, and queues a batch through the real CLI. Success
requires its `UserPromptSubmit` receipt. It stops its server and archives
its test thread; it does not change the installed plugin or mutate GitHub.
Folder and hook trust decisions persist in Codex's local settings.

The project stays in progress until the operator authorizes that temporary
folder and hook trust and the combined check passes. The test must also
observe new-head and finding events in a live review workflow before
completion; the automated producer tests and separate native transport
observations do not replace that acceptance check.
