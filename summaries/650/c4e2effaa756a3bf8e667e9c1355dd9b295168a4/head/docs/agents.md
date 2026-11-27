# Run agents in the console

Run `projector` to launch Claude and Codex, send follow-ups, answer inbox
items, and open the same conversation in the agent's native terminal.
Externally started processes keep their existing Projector inbox and messages.
The console controls execution only for sessions it launches.

## Install agent support

Install Projector with Python 3.11 or newer. Both `project` and `projector`
come with the core package; ordinary project commands require no SDK.
Install Claude Code or Codex separately and sign in through its native CLI.
Then prepare the optional worker environment:

```sh
projector setup
projector
```

Managed sessions require Claude Code 2.1.332 or newer, or Codex CLI 0.172.0
or newer. These versions were tested with Claude Agent SDK 0.2.189. Setup
pins that SDK in a separate virtual environment and copies Projector's Python
source there. Existing workers retain their environment across upgrades.
Run `projector setup` after upgrading to prepare the next environment.
Stopped sessions also resume with their original environment.

Projector uses the native runtime's authentication and handles no provider
OAuth tokens. Claude's published subscription guidance currently permits
SDK use under its paused billing change; provider policy can change.
Check [Claude's SDK subscription guidance](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
for current terms. Codex uses its native signed-in configuration.

## Launch and converse

Highlight a session and press `n` to enter the first prompt immediately.
The new session uses the highlighted session's working directory and provider
(Claude or Codex), with the provider's native default model. For another
kind of process, the provider defaults to Claude. When no session supplies
a directory, Projector uses its current directory.

Press Up from the prompt to change settings:

| Key | Action |
| --- | --- |
| `c`, `x` | Choose Claude or Codex, then choose a model |
| `m` | Choose a model for the current provider |
| `d` | Edit the working directory; Enter saves it |
| `n` | Set an optional session name; Enter saves it |

In the model menu, press `0` for the native default, `o` for Claude Opus,
or `s` for Claude Sonnet. Numbered choices reuse model IDs registered by
your Projector sessions. These choices do not check provider availability.
Press `t` to type another native model ID. Selecting a model returns
to the prompt. Switching providers clears the previous provider's model.
Down or Escape returns from a menu to the prompt. Escape discards edits
to a directory, name, or model ID without losing your prompt.

Enter adds a newline within the prompt; Ctrl-D launches it. Escape from the
prompt cancels the launch, and Ctrl-C cancels from any launch screen.
Prompts travel as structured data, so quotes, dollar signs, leading dashes,
and blank lines stay intact.

The runtime applies its native permission and sandbox settings, including
any automatic approvals you configured. Projector adds no permission bypass.
The form identifies those settings as the source of policy. The conversation
shows the effective policy when the runtime reports it; Claude reports its
mode when the first turn initializes. This is not a preflight permission audit.

Select a managed session and focus its panel with Enter. Its conversation
appears below its pull requests and inbox items. Move to the conversation
with the arrow keys, or press `c` to compose a message. Enter adds a line;
Ctrl-D sends it through the existing Projector message route. Follow-ups
wait in order while another turn runs. PgUp and PgDn scroll the conversation;
End follows the latest output.

| Key | Action |
| --- | --- |
| `c` | Compose a follow-up |
| `i` | Interrupt the current turn and pause later inputs |
| `s` | Send queued inputs after a pause |
| `x` | Stop the worker; retain history and pending input |
| `r` | Resume a stopped session, or return from native control |
| `o` | Open the native terminal after the current turn ends |
| `e`, `d` | Edit or remove a numbered queued message |
| `R`, `D` | Retry uncertain input, or confirm that it arrived |

A failed or interrupted turn stays distinct from a successful one. Completing
a turn does not mark a Projector project completed. Stopped sessions remain
in the list. A launch that fails before registration remains visible with
its error; `r` retries it and `x` removes it once its worker has exited.

## Answer the inbox

Projector questions, requests, review events, agent messages, and your answers
keep their existing inbox semantics. Discuss an ordinary item to ask about
it while leaving it open. Choose an option to answer it. Press `t` in a
question's drawer to type a different answer.

A native permission request becomes a question bound to the current runtime
request. **Deny** is recommended; **Allow once** authorizes that pending
action. Adding a note denies the original action and passes your note to
the agent, so a condition cannot be lost while granting the original action.
A native question returns your chosen or typed answer to its waiting tool.

Pending native requests offer answers and dismissal rather than discussion
or snoozing: their tool cannot handle another turn while it waits. Type a
clarification as the answer to continue that tool, or dismiss to deny or
cancel. Interrupting, stopping, or restarting the worker expires its old
native requests. A late answer cannot authorize a different action. Durable
Projector questions keep their normal lifecycle.

The worker supports Claude tool permissions and `AskUserQuestion`, Codex
command, file, and permission requests, Codex `requestUserInput`, and Codex MCP form
elicitation. Codex URL elicitation is declined, and Claude MCP elicitation
is not bridged. Complete those server setup steps in the native runtime. No answer grants a standing permission.

## Recover and open the native terminal

The console records input as queued, submitting, accepted, or finished.
If a worker dies after submission, it marks possibly accepted input uncertain
and pauses later inputs. It never automatically replays that input.

Open the saved native session and inspect its history. After you establish
that the input arrived, press `D`, select its number, and type `RECEIVED`.
To authorize another attempt instead, press `R` and type `RETRY`. Retrying
can repeat tool actions. Press `s` when you want the remaining queue to run.

Native handoff requires an idle worker with no pending native requests.
Interrupt and wait for the turn to end first if necessary. Press `o` to
release the SDK connection and open the saved native conversation. Projector
uses a tmux pane when available; otherwise it suspends the console until the
native CLI exits. Codex uses `codex --no-daemon resume` so exiting releases
its writer. Claude uses `claude --resume` with the saved ID.

Projector input remains queued during native control, including inbox answers
and review events. Close the native terminal, then press `r` to reacquire
managed control. The same history includes turns entered in the native
terminal. Press `s` to release the queue. Two consoles can observe a session,
but the worker and native terminal cannot control it simultaneously.

## Process lifetime and local state

Closing the console leaves its detached session workers running. Restarting
or upgrading the watch daemon also leaves their native connections running.
The daemon discovers launches and routes existing events; each worker owns
one connection and serializes its input.

Execution records live beside the session's watch state under
`$XDG_STATE_HOME/projector`, or `~/.local/state/projector`. Pre-registration
launches have separate records and a `worker.log`. Workers retain their native
resume ID, source environment, pending input, and outcomes. Damaged execution
state is reported and preserved rather than reset. Native transcripts remain
the source of history for native resume.

The display log trims toward 5,000 events and 8 MiB, independently of pending
commands and inbox delivery. Large display payloads show a truncated preview;
inspect native history for the full content. Console refresh reads new events
after a cursor rather than scanning a complete native transcript.

`project console` remains a compatibility entry point. Both console commands
require a terminal; agents must still obtain permission to run the human
interface. External processes retain their own execution controls. Windows
console support, a team scheduler, and automatic task retries are outside
this version.
