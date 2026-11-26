---
status: draft
priority: now
---

# Deliver inbox replies to active Codex turns

Let a user's inbox answer reach a working Codex session without waiting for that session to end its turn.

## 1. Problem

Projector records answers and discussions in the inbox, then its watch daemon sends them through `codex queue --resume`. This wakes idle sessions and preserves delivery across restarts. While a session is working, however, the queued prompt waits for another turn. A session that asks a question and waits for its answer can therefore wait on a message it cannot receive until it stops waiting.

The failure occurs after a successful submission: `codex_watch.Dispatcher._send` records `queued` when the CLI exits zero. That proves acceptance into the queue, not arrival in the active turn. The adapter also lets one pending batch hold back all later batches, so a queued watch event can delay an inbox answer.

## 2. Solution

Send user inbox lines to a working session through Codex's `turn/steer` API. Keep queue/resume for idle sessions and ordinary watch events. Reuse the inbox's claims, batch IDs, eligibility checks, and receipts, and separate inbox delivery from the background-event queue. Connect to the app server that owns the existing session; starting another app server is not a substitute for reaching the active turn.

This plan is `draft` because the local connection and active-turn acknowledgment still need the verification in § 6. Those are engineering gates, not choices left to the user.

### 2.1 Scope and authority

- **User inbox lines** use steering when the destination has an active turn. This includes answers, discussions, request starts and outcomes, dismissals, and notices that the user restored or did not understand. Classify them by the existing `inbox` event key, not by parsing their display text.
- **Watch events and agent messages** keep the queued path. Preserve their existing authority and message prefixes. A peer message does not become user input with additional authority because a steering transport exists.
- **Claude Code** keeps its current panel delivery. The console and a future phone client still record through the inbox and select no transport themselves.
- **Existing local Codex sessions** are the first supported target. This work does not launch agents, switch the selected server, change hook trust, or add an MCP dependency. Remote-server discovery is deferred; a daemon on the session's own machine can use its local connection.

### 2.2 Delivery flow

1. `offer` claims the user's owed line as it does today. Keep the stable item key and claim token, and renew the claim until the adapter can settle it.
2. Prepare separate `inbox` and `events` batches, preserving order within each lane. An accepted background batch does not prevent preparing or sending an inbox batch. Keep one live submission per session, and release its submission lock after the transport call returns.
3. Resolve and initialize a connection to the registered session's owning app server. Use the registered native Codex executable and the supported `app-server proxy` transport. Verify the session is loaded on that server. Do not resume it in another server to make an active turn appear there.
4. Read the active turn ID from the owning server. If a turn is active, persist the batch's steering destination before sending `turn/steer` with `threadId`, `expectedTurnId`, and the unchanged batch message as user text. Steer no other session or turn.
5. If the server explicitly rejects the expected turn because it ended or changed, read the current state once more. Steer a replacement active turn, or queue/resume the existing session if it is idle. Limit this race recovery to one attempt per dispatch; defer a second race to the next daemon pass.
6. Treat an accepted steering response as `steered`, distinct from a queued follow-up and from a consumed batch. Retain the batch and its claims until the existing exact-message hook acknowledgment or transcript confirmation proves that the destination accepted that batch into its conversation. The acknowledgment gate in § 6 must establish which native observation is reliable for steering.
7. Reuse `consume` and `settle` to write the receipt and mark the matching inbox line delivered. A revised or restored item with a new token settles nothing from the old batch. Two discussions remain ordered and independently deliverable.
8. When no turn is active, use the existing queue/resume path with the same batch ID. Once a transport might have accepted the message, do not send that batch through the other transport without proof that the first rejected it.

### 2.3 Connection and failure behavior

Steering needs access to the process that owns the turn, not just the session's persisted transcript. Extend registration with a verified local server locator only after the connection gate establishes how the trusted host supplies it. Validate its source and thread ownership; do not accept a locator from a repository file or a peer event. Pin a batch to the verified server and thread while it is pending.

| Condition | Behavior |
| --- | --- |
| Confirmed active turn and supported steering API | Steer the inbox batch |
| Confirmed idle session | Queue/resume as today |
| Explicit stale-turn rejection | Re-read once, then select active or idle delivery |
| Proxy unavailable, unsupported API, or ownership unverified | Keep the answer recorded and show that immediate inbox delivery is unavailable. Legacy queue delivery remains available, but report it as queued, with no claim of immediate delivery |
| Timeout or disconnect after a steering request may have been written | Keep the batch `uncertain`, renew its claims, and seek the exact acknowledgment or transcript marker. Do not automatically fall back to queue or replay it |
| Daemon restart after acceptance but before settlement | Recover the same pending batch and settle only with matching proof |
| User explicitly requests a retry of an uncertain batch | Preserve the existing warning that a retry can duplicate an accepted message |

An unavailable steering path must not look like a lost answer. `project watch status` and the console's existing delivery state must distinguish a queued follow-up, an accepted steer awaiting confirmation, and an uncertain submission. Diagnostics contain bounded causes, not prompts, credentials, or response bodies.

### 2.4 State and components

Extend the current pending-batch format compatibly. Older records retain the queued-delivery interpretation.

| Field | Meaning |
| --- | --- |
| `lane` | `inbox` or `events`; absent on legacy mixed batches |
| `transport` | `steer` or `queue`, selected before submission |
| `server` | Verified server identity or locator used for this batch; never secret material |
| `turn_id` | The turn supplied as `expectedTurnId`, or absent for queue delivery |
| `state` | Existing states plus `steered`, meaning accepted into an active turn but not yet confirmed consumed |

| Component | Change |
| --- | --- |
| `src/projector/codex_watch.py` | Add a bounded app-server client, verify capability and destination, select the transport, split batch lanes, recover steering attempts, and report delivery state |
| `src/projector/watch.py` | Schedule inbox delivery before background delivery within the existing worker bound, and expose the new state without blocking the daemon pass |
| `src/projector/inbox.py` | Reuse claims, owed lines, and settlement; change them only if the verified native acknowledgment requires it |
| `tests/test_codex_watch.py` | Add transport, ordering, race, restart, and failure tests using a fake app-server boundary |
| `tests/codex_smoke.py` | Add an opt-in disposable-session test that receives an answer during an active turn, while retaining idle-unload coverage |
| `docs/cli.md` and relevant skills | Explain immediate versus queued delivery and recovery. Tell an agent to check recorded inbox replies before waiting when steering is unavailable |

Before enabling the new path, classify legacy pending batches. Do not split, resend, or move an already accepted or uncertain mixed batch: that could deliver the same answer twice. Let its existing acknowledgment finish it and expose the temporary legacy queue state. Prepared batches that were definitely never submitted can move to separate lanes under the session lock, preserving their item keys.

## 3. Why this design

The inbox already records the user's intent and owns delivery claims. Keeping that store as the single source lets console, terminal, and phone actions share the fix. Replacing it with direct console RPC calls would leave other producers broken and create another delivery owner.

The native API can append input to an active turn without interrupting its tools. Interrupt-and-resume would cancel work merely to deliver an answer. Starting another server could create a second owner or observe an idle copy of the same history. A `--steer` flag on `codex queue` is not present in the inspected CLI.

Separate lanes remove the existing background-batch blockage while preserving inbox ordering. Keep one claim and receipt mechanism across both transports. A network or process failure can leave acceptance uncertain, so this plan does not promise exactly-once delivery without acknowledgment evidence.

## 4. Acceptance criteria

| Do | Expect | Bug caught |
| --- | --- | --- |
| Start a turn that posts a question and waits; answer through the console | The same active turn receives the answer and continues before it ends | The original queued-answer deadlock |
| Leave a watch-event batch queued, then answer an inbox item | The answer reaches the active turn while the older background batch remains queued | One pending batch blocking every transport |
| Answer for an idle or unloaded session | One queue/resume submission wakes the same session and its normal receipt settles the answer | Steering-only delivery breaking idle wakeups |
| End or replace the turn between observation and submission | One bounded re-read chooses the correct destination; no answer reaches an unrelated turn | A stale-turn race misrouting or duplicating input |
| Disconnect after request write but before its response | State is uncertain; no queue fallback or automatic second send occurs | A successful but unacknowledged steer being replayed |
| Crash after steering acceptance, then restart and observe its marker | The original batch settles once, with no resubmission | Lost receipts causing duplicate answers |
| Revise an item after its old batch was prepared | The old token settles nothing; the new line remains owed | Stale choices or discussions being treated as delivered |
| Discuss the same item twice during active work | Both messages arrive in order, and the item stays open | Discussions being reordered, collapsed, or mistaken for answers |
| Connect a proxy that does not own the session, or remove its capability | No steer is sent; the answer remains recorded and the lack of immediate delivery is visible | A second-server or silent-fallback failure |
| Upgrade with an accepted legacy mixed batch | Its existing delivery is preserved, with no migration replay | Upgrade-induced duplication |

See each new regression fail against the queued-only adapter or the specific failure injected at the transport boundary. The opt-in real-session smoke test must prove active-turn delivery and acknowledgment; fake RPC responses alone cannot establish either.

## 5. Cost

The daemon currently limits delivery to four worker jobs. Keep that global bound and move all connection and RPC waits outside the session lock and daemon pass. Use a short-lived connection per submission initially, with an overall deadline of five seconds and at most one state re-read. Do not poll the active turn when no inbox line is owed.

Measure ten live sessions with no owed inbox lines: the new path must create no RPC calls or processes. Under queued background traffic, measure an answer's latency to accepted steering, excluding model sampling and tool completion: target three seconds on a local host, and report missed targets with their cause.

## 6. Verification gates and open questions

These gates belong to the implementer. Resolve them in this plan before changing its status to `ready`.

1. **Reach the owning server.** Demonstrate a read-only connection from the adapter to an active disposable thread in the actual CLI and app hosts. Establish the locator's trusted source and verify active turn IDs. A local `app-server proxy` probe during planning did not complete initialization within eight seconds; the installed command's existence is not proof of connectivity. If a host offers no supported connection, leave that host explicitly unsupported for immediate steering and record the user-visible limitation.
2. **Prove native acknowledgment.** Establish whether a steered batch invokes `UserPromptSubmit`, is persisted to the rollout before an active turn can end, or exposes another reliable consumption event. Document the observation that closes the acceptance-versus-consumption gap. Do not settle an item solely because a mock returns `turnId`.
3. **Record real errors and capability behavior.** Capture actual stale-turn, no-active-turn, unsupported-method, and disconnected-server responses in disposable fixtures. Base definite-rejection recovery on those responses, not message substring guesses. Cache capability by the existing executable signature and invalidate it on upgrade.

No product decision is pending on the user. Remote server discovery, steering background watch events, and changing peer-message delivery are deliberately deferred.

## 7. Background

- Codex CLI `0.172.0` exposes `app-server proxy` and generates a `TurnSteerParams` schema requiring `threadId`, `expectedTurnId`, and `input`. `TurnSteerResponse` returns `turnId`. Its `queue --help` exposes queue/resume without a steering flag.
- The [Codex App Server guide](https://developers.openai.com/codex/app-server/#steer-an-active-turn) documents active-turn steering and rejection when no active turn exists. [Queue and Steer guidance](https://developers.openai.com/blog/mastering-codex-remote-for-engineering#2-learn-the-difference-between-queue-and-steer) distinguishes a later follow-up from input to ongoing work.
- `Dispatcher._send`, `prepare`, and `acknowledge` in `src/projector/codex_watch.py` establish the current queue-only submission, single pending-batch gate, and exact-message receipt. `offer`, `renew`, `eligible`, and `settle` carry the existing inbox claim and authority rules.
- A local observation during planning found an inbox discussion recorded and claimed, with its batch accepted as `queued` and no submission error, while its destination turn kept working. Reading the recorded inbox reply directly recovered the user's feedback; it did not prove transport consumption.
