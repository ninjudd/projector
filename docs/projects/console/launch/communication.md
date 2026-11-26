# Communication references

Projector keeps its existing inbox, agent messages, and watch delivery.
Tripping is a reference for execution tracking and recovery. Its message bus
does not become a dependency or a second place to send Projector messages.

## What Tripping implements

Tripping's [message envelope](https://github.com/ninjudd/tripping/blob/main/src/team/envelope.ts)
has `kind`, `thread`, `reply_to`, `subject`, `body`, and optional artifact
paths. A new task gets its own correlation thread. Results name that thread;
the agent's native turn ending does not close the task.

Its [mailbox](https://github.com/ninjudd/tripping/blob/main/src/team/mailbox.ts)
stores messages as files. Sending stages a file and renames it into the
recipient's inbox. Reading a task claims it by moving it to `working/`.
Sending a result writes the result before moving the matching task to
`archive/`. Reading other message kinds archives them immediately; those
messages deliberately have at-most-once read semantics.

`bus.jsonl` records message metadata and lifecycle events, without bodies.
The [dispatcher](https://github.com/ninjudd/tripping/blob/main/src/team/dispatch.ts)
joins results by thread. The
[recovery code](https://github.com/ninjudd/tripping/blob/main/src/team/respawn.ts)
uses the audit to close tasks whose result already exists, redeliver held
tasks within a cap, or park them in `dead/`. A synthetic failure result
lets a waiting coordinator finish rather than wait forever.

## What Projector needs from it

| Concern | Projector today | Decision for managed sessions |
| --- | --- | --- |
| Durable addressing and reply correlation | `agents.py` stores message IDs, `reply_to`, and `thread`, with local and relayed delivery | Reuse them. Keep sender identity and trust labels intact |
| Human questions, requests, and answers | `inbox.py` stores item state, discussion, claims, and delivery acknowledgments | Reuse them for Projector items; bridge native requests to these items |
| Transport acceptance | `codex_watch.py` tracks batches, acknowledgments, and transcript evidence when an acknowledgment is lost | Extend this boundary for managed runtimes; do not equate accepted delivery with completed work |
| Claimed work and explicit results | Messages can be read and replied to, but have no general task claim, typed result, or fan-in contract | Track runtime commands and turn outcomes in this project. Defer a public delegated-task API and team scheduler |
| Failure recovery | Inbox claims and native delivery recover their own state; there is no managed worker lifecycle | Add worker ownership, command reconciliation, visible failure, and an explicit uncertain state |
| Failed work and retry limits | No general dead-letter task queue | Retain failed or uncertain commands for inspection. Retry only proven unaccepted input automatically; defer autonomous task reruns |
| Artifacts and audit | Message text and native transcripts carry context; no shared typed artifact list | Keep native tool references and correlated events. Defer a public artifact envelope until a caller needs it |

## Limits to carry into the implementation

- **Three different milestones.** A message can be delivered without the
  recipient completing a turn, and a turn can finish without a delegated
  task being done. A session status must not imply all three succeeded.
- **Durable result before cleanup.** Save a completed outcome before
  releasing the command or delivery claim. Repeating recovery must preserve
  the outcome rather than repeat the work.
- **Audit is not a transaction.** Tripping writes the result, audit line,
  and task closure separately. Its intent is useful, but copying those
  operations does not prove crash-safe deduplication. Test Projector's
  failure boundaries against durable command and native-session evidence.
- **A read is not completion.** Tripping archives non-task messages on read.
  Keep Projector's existing delivery guarantees instead of adopting those
  semantics for answers or discussion.
- **Correlation needs provenance.** Tripping's result join matches a
  thread. For runtime completion, also check the expected session, worker
  generation, and native operation. An unrelated agent reply cannot finish
  a command merely by naming its thread.
- **Recovery must be visible.** Show pending, accepted, completed, failed,
  and uncertain execution separately. Preserve malformed records for
  diagnosis while allowing unrelated valid records to progress.
- **No automatic worktree edits.** Tripping's broader respawn machinery
  includes worktree checkpoints. Session recovery here does not commit,
  abort a merge, or reset a user's checkout.
