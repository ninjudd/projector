# Run agents in the console

Run `projector` to launch Claude and Codex, send follow-ups, answer inbox
items, and open the same conversation in the agent's native terminal.
Externally started processes keep their existing Projector inbox and messages.
The console controls execution only for sessions it launches.

To run the console directly from a source checkout, use Python 3.11 or newer
from the repository root:

```sh
PYTHONPATH=src python3 -m projector.console
```

This uses your checkout's code and existing sessions without changing the
installed `projector` command. Add `setup` to prepare agent support from that
checkout.

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
kind of process, the provider defaults to Claude. Linked worktrees appear
under their main checkout's heading in the session list. The new session
starts in the highlighted session's actual working directory. When no
session supplies a directory, Projector uses its current directory.

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

Enter launches the prompt. Shift-Enter adds a newline when your terminal
reports it separately. Ctrl-J also adds a newline. Projector enables the
[Kitty keyboard protocol](https://sw.kovidgoyal.net/kitty/keyboard-protocol/)
in terminals that support it and accepts xterm's
modified-key encoding. In terminals that send the same bytes for Enter and
Shift-Enter, use Ctrl-J. Pasted line breaks stay in the draft until you
press Enter. Escape from the prompt cancels the launch, and Ctrl-C cancels
from any launch screen.
Prompts travel as structured data, so quotes, dollar signs, leading dashes,
and blank lines stay intact.

The runtime applies its native permission and sandbox settings, including
any automatic approvals you configured. Projector adds no permission bypass.
Use Up, then `p`, to inspect the settings and effective policy reported by
the runtime. Claude reports its mode when the first turn initializes.

In the session list, Backspace collapses the selected directory group.
Enter or Right expands it again. Click the directory heading to toggle it.

Select a managed session and press Enter, or click it, to open its chat.
The composer has focus immediately and stays ready after you send a message.
Enter sends through the existing Projector message route. Shift-Enter or
Ctrl-J adds a line. Follow-ups wait in order while another turn runs.

Your messages appear on a gray background. Replies use ordinary text, without
speaker labels or a conversation heading. The prompt shows `…` while a turn
is active and `❯` when it is idle. Successful turns change the prompt without
adding a completion message. Failed and interrupted turns keep their visible
outcome.

Press Up to show actions in the existing hint line. The menu adds no rows and
does not move your prompt. Press Down or Escape to return to the same draft.
When all choices do not fit, Left and Right show the other choices in that
same line. Launch settings use this menu too.

| Key after Up | Action |
| --- | --- |
| `i` | Interrupt the current turn and pause later messages |
| `x` | Stop the worker while retaining history and waiting messages |
| `r` | Resume a stopped worker or return from native control |
| `o` | Open the agent's native terminal |
| `s` | Continue messages after a pause |
| `q` | Inspect, edit, or remove a waiting message |
| `k` | Check a message whose delivery Projector lost track of |
| `b` | Open the session's inbox and pull requests |
| `p` | Inspect runtime details and native policy |
| `h` | Return to the session list |
| `f` | Follow the latest output after scrolling back |

The menu shows the actions that apply to the session's current state.
PgUp, PgDn, and the mouse wheel scroll history while the composer stays in
place. Your draft survives a trip to the inbox, menus, or another session.
Escape closes an inbox or message card and returns to that draft. It also
cancels an edit to a waiting message without saving it.

Click rows and menu choices to use them. Drag to select visible text without
activating the row underneath. Projector keeps that view fixed until you
clear the selection, while the agent keeps running. Releasing the drag copies
the selection through the local clipboard where available, or OSC 52 where
your terminal permits it. Ctrl-C copies selected text without interrupting
or quitting. Escape clears the selection. Your terminal's selection override
remains available too.

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

If the worker dies while handling a message, Projector holds it rather than
risk sending it twice. A **Check message** card and a hint appear only while
this needs your attention. Press Up, then `k`, or click the card to inspect
the full message.

Choose **Open agent conversation** to check the native history. If the
message is there, choose **Already reached the agent**. If it is missing,
choose **Send again**, then confirm that choice. The confirmation explains
that sending again can repeat file edits or commands. Enter selects Cancel
by default. You do not type message numbers or confirmation words.

Other messages continue after every held message is checked. A choice made
in another console invalidates stale buttons. Expired or changed inbox
events keep their delivery checks and cannot be replayed as a new event.

Native handoff is reversible and keeps the same conversation. It requires
an idle worker with no pending native requests. Interrupt and wait for the
turn to end first if necessary. Press Up, then `o`, to release the SDK
connection and open the saved native conversation. Projector uses a tmux
pane when available; otherwise it suspends the console until the native CLI
exits. Codex uses `codex --no-daemon resume` so exiting releases its writer.
Claude uses `claude --resume` with the saved ID.

Projector input remains queued during native control, including inbox answers
and review events. Close the native CLI to return to Projector. When it ran
in a tmux pane, press Up, then `r`, to reacquire managed control. When it
temporarily replaced the console, Projector reacquires control automatically
after it exits. The same history includes turns entered in the native
terminal. Press Up, then `s`, to continue waiting messages. Two consoles can
observe a session, but the worker and native terminal cannot control it
simultaneously.

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
