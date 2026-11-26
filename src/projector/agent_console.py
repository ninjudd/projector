from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from . import agents, console, inbox, rows, runtime, watch
from .core import ProjectorError, UsageError
from .runtime_store import Store

Line = console.Line
Field = console.Field


class Console(console.Console):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtimes = {}
        self.launch_form = {}
        self.pending_native = None
        self.stores = {}
        self.cursors = {}
        self.chats = {}
        self.next_runtime = float("-inf")

    def due(self, now):
        work = super().due(now)
        viewed = self.viewed()
        if viewed is not None and viewed.name in self.runtimes and now >= self.next_runtime:
            self.next_runtime = now + 0.2
            if work is None:
                work = console.Work(inbox=False, rows=False, signatures={})
        return work

    def store(self, session):
        if session not in self.stores:
            self.stores[session] = Store(watch.session_folder(self.root, session))
        return self.stores[session]

    def load(self, work):
        found = super().load(work)
        if found.live is not None:
            managed = runtime.sessions(self.root)
            for name, current in managed.items():
                record = agents.record(self.root, name) or {}
                previous = found.live.get(name)
                folder = inbox.store_root() / name
                label = record.get("name") or current.get("name") or agents.short_id(name)
                host = current.get("backend") or record.get("host") or "process"
                cwd = current.get("cwd") or record.get("cwd", "")
                place = " ".join(filter(None, (record.get("repo"), record.get("branch"))))
                summary = current.get("state", "") + (": " + current["error"] if current.get("error") else "")
                signature = previous[5] if previous else console.signature(folder)
                items = previous[6] if previous else inbox.load_items(folder)
                found.live[name] = (label, host, place, summary, cwd, signature, items)
                if name not in work.signatures:
                    found.rows[name] = rows.rows_for(self.root, name)
                if name not in found.entries:
                    found.entries[name] = console.repo_inbox.repo_items(self.root, name, everything=True)
            for launch in runtime.launches(self.root):
                if launch.get("session") in managed or launch.get("state") == "live":
                    continue
                name = "launch-" + launch["id"]
                state = launch.get("state", "starting")
                if state == "starting" and time.time() - launch.get("spawned_at", time.time()) > 120:
                    state = "Launch did not register; inspect the worker log"
                found.live[name] = (launch.get("name") or "New session", launch["backend"], "",
                    launch.get("error") or state, launch["cwd"], (), [])
                found.rows[name] = []
                found.entries[name] = []
            found.runtimes = managed
        viewed = self.viewed()
        if viewed is not None and viewed.name in self.runtimes:
            name = viewed.name
            try:
                events = self.store(name).events(self.cursors.get(name, 0))
                found.runtime_events = (name, events)
            except ProjectorError as error:
                found.runtime_error = str(error)
        return found

    def apply(self, found):
        super().apply(found)
        if hasattr(found, "runtimes"):
            self.runtimes = found.runtimes
        if hasattr(found, "runtime_error"):
            self.status = found.runtime_error
        if hasattr(found, "runtime_events"):
            name, events = found.runtime_events
            chat = self.chats.setdefault(name, [])
            for event in events:
                kind, payload = event["kind"], event["payload"]
                if kind == "text" and chat and chat[-1][0] == "agent":
                    chat[-1] = ("agent", chat[-1][1] + payload.get("text", ""))
                elif kind in ("text", "user"):
                    chat.append(("agent" if kind == "text" else "you", payload.get("text", "")))
                elif kind == "tool":
                    item = payload.get("item", {})
                    label = item.get("name") or item.get("type") or "Tool"
                    value = (item.get("input") or item.get("command") or item.get("content")
                             or item.get("output") or payload.get("preview") or "")
                    chat.append(("tool", f"{label}: {str(value)[:4096]}"))
                elif kind == "turn_end":
                    chat.append(("status", payload.get("outcome", "") + (": " + str(payload["error"]) if payload.get("error") else "")))
                self.cursors[name] = event["sequence"]
            self.chats[name] = chat[-1000:]

    def conversation_lines(self, name, width, selected):
        if name in self.runtimes:
            return self.runtime_lines(name, width, selected)
        return super().conversation_lines(name, width, selected)

    def key(self, name):
        if name == "\x04":
            name = "ctrl-d"
        try:
            with self.lock:
                if self.field is None and self.view == "home" and name == "n":
                    self.launch_form = {"backend": "claude", "cwd": str(Path.cwd()), "name": "", "model": ""}
                    self.field = Field("launch-backend", None, "Agent (claude or codex): ", "claude", 6)
                    return
                if self.field is None and self.view == "panel" and self.drawer is None and self.session in self.runtimes:
                    if name == "v":
                        return
                    if name == "c":
                        self.selected = console.CONVERSATION
                        self.follow = True
                        self.field = Field("chat", None, "Message (Enter: newline; Ctrl-D: send): ")
                        return
                    action = {"i": "interrupt", "x": "stop", "o": "handoff", "r": "resume", "s": "send"}.get(name)
                    if action:
                        if action == "handoff" and self.runtimes[self.session].get("state") == "handed-off":
                            raise UsageError("Close the native terminal, then press r to return")
                        runtime.control(self.root, self.session, action)
                        if action == "handoff":
                            self.pending_native = self.session
                        self.ensure()
                        self.refresh_now.set()
                        return
                    if name in ("e", "d", "R", "D"):
                        self.field = Field("queue-" + name, None, "Input number: ")
                        return
            super().key(name)
        except (ProjectorError, OSError) as error:
            self.status = str(error)

    def field_key(self, name):
        typing = self.field
        if typing is None:
            return
        purpose = typing.purpose
        if purpose == "launch-backend" and name in ("tab", "up", "down"):
            typing.text = "codex" if typing.text == "claude" else "claude"
            typing.cursor = len(typing.text)
            return
        multiline = purpose in ("launch-prompt", "chat", "queue-edit")
        if multiline and name == "enter":
            typing.text = typing.text[:typing.cursor] + "\n" + typing.text[typing.cursor:]
            typing.cursor += 1
            return
        if (multiline and name == "ctrl-d") or (not multiline and name == "enter"
                                                and purpose not in ("queue-confirm", "queue-confirm-received")
                                                and purpose.startswith(("launch-", "queue-"))):
            if purpose.startswith("launch-"):
                field = purpose.removeprefix("launch-")
                self.launch_form[field] = typing.text
                if field != "prompt":
                    following = {"backend": "cwd", "cwd": "name", "name": "model", "model": "prompt"}[field]
                    value = self.launch_form.get(following, "")
                    prompts = {"cwd": "Working directory: ", "name": "Name (optional): ", "model": "Model (native default if empty): ",
                               "prompt": "Prompt (Enter: newline; Ctrl-D: launch): "}
                    self.field = Field("launch-" + following, None, prompts[following], value, len(value))
                    return
                runtime.launch(self.root, self.launch_form["backend"].strip(), self.launch_form["cwd"],
                    self.launch_form["prompt"], name=self.launch_form["name"].strip(), model=self.launch_form["model"].strip(),
                    parent=os.environ.get("PROJECTOR_SESSION", ""))
                self.ensure()
            elif purpose == "chat":
                runtime.send(self.root, self.session, typing.text)
                self.wake(self.session, {})
            elif purpose == "queue-edit":
                runtime.validate_text(typing.text)
                self.store(self.session).edit(typing.target, typing.text)
            else:
                state = "uncertain" if purpose in ("queue-R", "queue-D") else "queued"
                commands = self.store(self.session).commands((state,))
                if not typing.text.isdecimal() or not 1 <= int(typing.text) <= len(commands):
                    raise UsageError("Choose an input number from the queue")
                command = commands[int(typing.text) - 1]
                if purpose in ("queue-e", "queue-d") and command["source"]:
                    source = command["source"]
                    item = inbox.find(self.session, source[1]) if source[:1] == ["inbox"] else {}
                    if item.get("kind") != "message" or item.get("pr"):
                        raise UsageError("Use the item's inbox or pull request controls for this event")
                if purpose == "queue-e":
                    self.field = Field("queue-edit", command["id"], "Edit queued input (Ctrl-D: save): ",
                                       command["text"], len(command["text"]))
                    return
                if purpose == "queue-R":
                    self.field = Field("queue-confirm", command["id"], "Retry can repeat tool actions. Type RETRY: ")
                    return
                if purpose == "queue-D":
                    self.field = Field("queue-confirm-received", command["id"],
                                       "Check native history, then type RECEIVED to confirm this input arrived: ")
                    return
                else:
                    if command["source"] and command["source"][:1] != ["inbox"]:
                        raise UsageError("Use the item's inbox or pull request controls for this event")
                    self.store(self.session).edit(command["id"], None)
                if command["source"][:1] == ["inbox"]:
                    with contextlib.suppress(ProjectorError):
                        inbox.settle(self.session, command["source"][1], command["source"][3], delivered=False)
            self.field = None
            self.next_inbox = float("-inf")
            self.refresh_now.set()
            return
        if purpose == "queue-confirm" and name == "enter":
            if typing.text != "RETRY":
                raise UsageError("Type RETRY to confirm, or Escape to cancel")
            self.store(self.session).resolve(typing.target, retry=True)
            self.field = None
            return
        if purpose == "queue-confirm-received" and name == "enter":
            if typing.text != "RECEIVED":
                raise UsageError("Type RECEIVED only after checking native history, or Escape to cancel")
            runtime.control(self.root, self.session, "confirm:" + typing.target)
            self.field = None
            return
        super().field_key(name)

    def footer(self):
        if self.field is not None and self.field.purpose in ("launch-prompt", "chat", "queue-edit"):
            return [Line([("Enter adds a line · Ctrl-D sends · Esc cancels", "subtle")])]
        return super().footer()

    def home_lines(self):
        lines, focus = super().home_lines()
        if self.field is not None and self.field.purpose.startswith("launch-"):
            lines = [Line([("New agent session", "bold")]),
                     Line([("Permissions: native runtime settings; actions that need approval go to your inbox.", "subtle")])]
            lines.extend(Line([(key + ": " + value, "subtle")]) for key, value in self.launch_form.items())
            lines.append(self.form_line(self.field))
            if self.status:
                lines.append(Line([(self.status, "error")]))
            return lines, (0, len(lines) - 1)
        lines.append(Line([("n new agent session", "subtle")]))
        if self.status:
            lines.append(Line([(self.status, "error")]))
        return lines, focus

    @staticmethod
    def form_line(typing):
        text = typing.text.replace("\n", " ↵ ")
        cursor = len(typing.prompt) + len(typing.text[:typing.cursor].replace("\n", " ↵ "))
        return Line([(typing.prompt, "accent"), (text, "")], spot=True, cursor=cursor)

    def runtime_lines(self, name, width, selected):
        lines = []
        current = self.runtimes[name]
        if current.get("state") == "handed-off":
            lines.append(Line([("The native terminal owns this session. Projector input waits until you return.", "warning")]))
        policy = current.get("policy", {})
        if policy:
            sandbox = policy.get("sandbox", {})
            sandbox = sandbox.get("type", "native settings") if isinstance(sandbox, dict) else sandbox
            lines.append(Line([(f"Approvals: {policy.get('approval', 'native settings')} · sandbox: {sandbox}", "subtle")]))
        lines.append(Line([("c chat · i interrupt · x stop · o native terminal · r return/resume · s send queued", "accent")]))
        lines.append(Line([("e edit queued · d remove queued · R retry uncertain · D confirm received", "subtle")]))
        try:
            store = self.store(name)
        except ProjectorError as error:
            return [*lines, Line([(str(error), "error")])]
        if current.get("paused"):
            lines.append(Line([("Queued work is paused. Press s when you want to send it.", "warning")]))
        result = store.value("control_result", {})
        if result.get("error"):
            lines.append(Line([(result["error"], "error")]))
        for state in ("queued", "uncertain"):
            for at, command in enumerate(store.commands((state,)), 1):
                preview = command["text"].replace("\n", " ↵ ")[:160]
                lines.append(Line([(f"{state} {at}: {preview}", "warning" if state == "uncertain" else "subtle")]))
        lines.append(Line([("Conversation", "bold")], selected))
        for who, text in self.chats.get(name, []):
            for paragraph in text.splitlines() or [""]:
                for part in textwrap.wrap(paragraph, max(1, width - 8)) or [""]:
                    lines.append(Line([(who + ": ", "subtle"), (part, "accent" if who == "you" else "")]))
        if self.field is not None and self.field.purpose in ("chat", "queue-edit"):
            lines.append(self.form_line(self.field))
        return lines


class Screen(console.Screen):
    def draw(self):
        session = self.console.pending_native
        if session and self.console.runtimes.get(session, {}).get("state") == "handed-off":
            self.console.pending_native = None
            current = self.console.runtimes[session]
            command = [sys.executable, "-m", "projector.native_handoff", str(self.console.root), session]
            try:
                if os.environ.get("TMUX") and shutil.which("tmux"):
                    routed = ["env", "PYTHONPATH=" + str(Path(__file__).parent.parent), *command]
                    subprocess.run(["tmux", "split-window", "-h", "-c", current["cwd"], shlex.join(routed)], check=True)
                else:
                    self.curses.def_prog_mode()
                    self.curses.endwin()
                    try:
                        subprocess.run(command, check=False)
                    finally:
                        self.curses.reset_prog_mode()
                        self.window.refresh()
                    runtime.control(self.console.root, session, "resume")
            except (OSError, subprocess.SubprocessError, ProjectorError) as error:
                self.console.status = str(error)
        super().draw()


def main(wake, ensure=lambda: None):
    import curses
    os.environ.setdefault("ESCDELAY", "25")
    instance = Console(watch.state_root(), wake=wake, ensure=ensure)
    try:
        return curses.wrapper(lambda window: Screen(window, instance, curses).run())
    except KeyboardInterrupt:
        return 0
