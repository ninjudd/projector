from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

from . import agents, console, inbox, native_handoff, rows, runtime, watch
from .core import ProjectorError, UsageError
from .runtime_store import Store

Line = console.Line
Field = console.Field
CONVERSATION = "runtime:conversation"
MESSAGE = "runtime:message:"


@dataclass(frozen=True)
class PendingMessage:
    command: dict


class InlineField(Field):
    @property
    def inline(self):
        return True


class Console(console.Console):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.runtimes = {}
        self.launch_form = {}
        self.launch_prompt = None
        self.launch_models = {}
        self.pending_native = None
        self.stores = {}
        self.cursors = {}
        self.chats = {}
        self.streaming = set()
        self.runtime_views = {}
        self.next_runtime = float("-inf")
        self.follow = True
        self.drafts = {}
        self.queue_open = False
        self.recovery_retry = None
        self.menu_page = 0

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
                signature = previous[4] if previous else console.signature(folder)
                items = previous[5] if previous else inbox.load_items(folder)
                changed = console.describe(self.root, name, "managed", record)[3]
                found.live[name] = (label, host, cwd, changed, signature, items)
                if name not in work.signatures:
                    found.rows[name] = rows.rows_for(self.root, name)
                if (work.rows or name not in work.signatures) and name not in found.artifacts:
                    artifacts = console.read_artifacts(self.root, name)
                    if artifacts is not None:
                        found.artifacts[name] = artifacts
                if name not in found.entries:
                    found.entries[name] = console.repo_inbox.repo_items(self.root, name, everything=True)
            for launch in runtime.launches(self.root):
                if launch.get("session") in managed or launch.get("state") in ("live", "cancelled"):
                    continue
                name = "launch-" + launch["id"]
                found.live[name] = (launch.get("name") or "New session", launch["backend"],
                    launch["cwd"], launch.get("created_at", 0) * 1000, (), [])
                found.rows[name] = []
                found.entries[name] = []
            found.runtimes = managed
        viewed = self.viewed()
        if viewed is not None and viewed.name in self.runtimes:
            name = viewed.name
            try:
                events = self.store(name).events(self.cursors.get(name, 0))
                found.runtime_events = (name, events)
                store = self.store(name)
                found.runtime_view = (name, {"commands": store.commands(("queued", "uncertain")),
                    "control_result": store.value("control_result", {}),
                    "pending_requests": store.value("pending_requests", {})})
            except ProjectorError as error:
                found.runtime_error = str(error)
        return found

    def apply(self, found):
        if hasattr(found, "runtimes"):
            self.runtimes = found.runtimes
        if hasattr(found, "runtime_view"):
            name, view = found.runtime_view
            self.runtime_views[name] = view
        super().apply(found)
        if self.session in self.drafts:
            self.drafts[self.session].prompt = self.message_prompt(self.session)
        if hasattr(found, "runtime_error"):
            self.status = found.runtime_error
        if hasattr(found, "runtime_events"):
            name, events = found.runtime_events
            chat = self.chats.setdefault(name, [])
            for event in events:
                kind, payload = event["kind"], event["payload"]
                if kind == "text" and name in self.streaming and chat and chat[-1][0] == "agent":
                    chat[-1] = ("agent", (chat[-1][1] + payload.get("text", ""))[-65536:])
                elif kind in ("text", "user"):
                    chat.append(("agent" if kind == "text" else "you", payload.get("text", "")))
                elif kind == "tool":
                    item = payload.get("item", {})
                    label = item.get("name") or item.get("type") or "Tool"
                    value = (item.get("input") or item.get("command") or item.get("content")
                             or item.get("output") or payload.get("preview") or "")
                    chat.append(("tool", f"{label}: {str(value)[:4096]}"))
                elif kind == "turn_end":
                    if payload.get("outcome") != "completed" or payload.get("error"):
                        chat.append(("status", payload.get("outcome", "") + (": " + str(payload["error"]) if payload.get("error") else "")))
                if kind == "text":
                    self.streaming.add(name)
                elif kind in ("turn_end", "user", "tool"):
                    self.streaming.discard(name)
                self.cursors[name] = event["sequence"]
                if payload.get("truncated"):
                    chat.append(("status", "Display truncated; inspect native history for the full content."))
            retained = []
            size = 0
            for entry in reversed(chat[-1000:]):
                size += len(entry[1])
                if size > 2 * 1024 * 1024:
                    break
                retained.append(entry)
            self.chats[name] = list(reversed(retained))

    def entries(self):
        entries = super().entries()
        viewed = self.viewed()
        if viewed is not None:
            entries += [(MESSAGE + command["id"], PendingMessage(command))
                        for command in self.runtime_views.get(viewed.name, {}).get("commands", [])
                        if command["state"] == "uncertain" or self.queue_open]
        return entries

    def open_panel(self, name):
        super().open_panel(name)
        self.queue_open = False
        if name in self.runtimes:
            self.follow = True
            self.compose()

    def compose(self):
        self.close_drawer()
        self.queue_open = False
        self.selected = CONVERSATION
        self.field = self.drafts.setdefault(self.session, InlineField("chat", None, self.message_prompt(self.session)))
        self.field.prompt = self.message_prompt(self.session)

    def close_drawer(self):
        self.recovery_retry = None
        super().close_drawer()

    def open_drawer(self, key):
        if self.field is not None and self.field.purpose in ("chat", "chat-options", "chat-info"):
            self.field = None
        super().open_drawer(key)

    def click(self, target):
        with self.lock:
            try:
                self.click_target(target)
            except (ProjectorError, OSError) as error:
                self.status = str(error)

    def click_target(self, target):
        if (target.control is not None and target.entry is None and target.session == self.session
                and self.field is not None and self.field.purpose in ("chat-options", "launch-settings", "launch-models")):
            self.status = ""
            target.control.act()
            return
        if (not target.group and target.entry is None and target.session in self.runtimes
                and target.session in self.sessions):
            self.open_panel(target.session)
            return
        if (self.field is not None and self.field.purpose in ("chat", "chat-options", "chat-info")
                and target.session == self.session and target.entry in dict(self.entries())):
            self.field = None
        super().click(target)

    def keys(self):
        keys = super().keys()
        if self.view == "panel" and self.session in self.runtimes:
            keys.append(CONVERSATION)
        return keys

    def ring(self):
        ring = super().ring()
        if self.session in self.runtimes:
            ring.append((CONVERSATION, None))
        return ring

    def press_focus(self):
        if self.selected_key() == CONVERSATION:
            self.compose()
        else:
            super().press_focus()

    def panel_key(self, name):
        if self.session in self.runtimes and self.drawer is not None and name in ("esc", "left", "0"):
            cancel = next((control for control in self.controls() if control.key == "0"), None)
            if name == "0" and cancel is not None:
                self.press(cancel)
            else:
                self.compose()
            return
        super().panel_key(name)

    def scroll(self, name):
        if self.view == "panel" and self.selected_key() == CONVERSATION:
            self.follow = name in ("end", "G")
        super().scroll(name)

    def panel_viewport(self, width, room):
        if self.session in self.runtimes and self.selected_key() == CONVERSATION:
            draft = self.field if self.field is not None and self.field.purpose == "queue-edit" else self.drafts.get(self.session)
            if draft is not None:
                body, _ = self.panel_lines(width)
                input_lines = self.field_lines(draft, max(1, width), active=self.field is draft)
                count = min(len(input_lines), max(1, room // 2))
                cursor = next(at for at, line in enumerate(input_lines) if line.spot)
                first = max(0, min(cursor - count + 1, len(input_lines) - count))
                shown_input = input_lines[first:first + count]
                if self.field is not draft:
                    for line in shown_input:
                        line.cursor = None
                history, available = body[:-len(input_lines)], room - count
                self.room = max(1, available)
                limit = max(0, len(history) - available)
                self.offset = limit if self.follow else max(0, min(self.offset, limit))
                if self.offset == limit:
                    self.follow = True
                shown = history[self.offset:self.offset + available]
                return [*shown, *(Line([]) for _ in range(available - len(shown))), *shown_input]
        return super().panel_viewport(width, room)

    def panel_lines(self, width):
        lines, focus = super().panel_lines(width)
        session = self.viewed()
        if session is not None and session.name.startswith("launch-"):
            current = next((item for item in runtime.launches(self.root)
                            if "launch-" + item["id"] == session.name), {})
            lines += [Line([(current.get("error") or current.get("state", "starting"), "warning")]),
                      Line([("r retry launch · x remove launch", "subtle")])]
        if session is not None and session.name in self.runtimes:
            if not self.entries():
                lines = [Line([(session.label, "subtle")])]
            start = len(lines)
            selected = self.view == "panel" and self.selected_key() == CONVERSATION
            lines += self.runtime_lines(session.name, width, selected)
            if selected:
                focus = (start, len(lines) - 1)
                cursor = next((at for at in range(start, len(lines)) if lines[at].spot), None)
                if cursor is not None:
                    focus = (cursor, cursor)
        return lines, focus

    def key(self, name):
        try:
            with self.lock:
                if self.field is None and self.view == "panel" and self.session and self.session.startswith("launch-"):
                    if name in ("r", "x"):
                        runtime.change_launch(self.root, self.session.removeprefix("launch-"), retry=name == "r")
                        self.ensure()
                        self.next_inbox = float("-inf")
                        self.refresh_now.set()
                        return
                if self.field is None and self.view == "home" and name == "n":
                    session = self.viewed()
                    backend = session.host if session is not None and session.host in ("claude", "codex") else "claude"
                    cwd = session.cwd if session is not None and session.cwd else str(Path.cwd())
                    self.launch_form = {"backend": backend, "cwd": cwd, "name": "", "model": ""}
                    self.launch_models = {host: [] for host in ("claude", "codex")}
                    for record in agents.records(self.root).values():
                        host, model = record.get("host"), record.get("model")
                        if host in ("claude", "codex") and isinstance(model, str) and agents.MODEL_ID.fullmatch(model):
                            if model not in self.launch_models[host]:
                                self.launch_models[host].append(model)
                    self.launch_prompt = InlineField("launch-prompt", None, "Prompt: ")
                    self.field = self.launch_prompt
                    self.status = ""
                    return
            super().key(name)
        except (ProjectorError, OSError) as error:
            self.status = str(error)

    def field_key(self, name):
        typing = self.field
        if typing is None:
            return
        purpose = typing.purpose
        if purpose in ("chat-options", "launch-settings", "launch-models") and name in ("left", "right"):
            self.menu_page += -1 if name == "left" else 1
            return
        if purpose in ("chat-options", "chat-info"):
            if name in ("esc", "down", "enter", "ctrl-j"):
                self.compose()
            elif name == "ctrl-c":
                self.go_home()
            else:
                choice = next((one for one in self.session_controls() if one.key == name), None)
                if choice is not None:
                    choice.act()
            return
        if purpose == "chat" and name == "up":
            self.menu_page = 0
            self.field = InlineField("chat-options", None, "")
            return
        if purpose == "chat" and name in ("pageup", "pagedown"):
            self.scroll(name)
            return
        if purpose == "queue-edit" and name in ("esc", "ctrl-c"):
            self.compose()
            return
        multiline = purpose in ("launch-prompt", "chat", "queue-edit")
        if name == "ctrl-j" and not multiline:
            name = "enter"
        if multiline and name in ("ctrl-j", "shift-enter"):
            typing.text = typing.text[:typing.cursor] + "\n" + typing.text[typing.cursor:]
            typing.cursor += 1
            return
        if purpose.startswith("launch-"):
            self.launch_key(name)
            return
        if name == "enter" and purpose in ("chat", "queue-edit"):
            if purpose == "chat":
                runtime.send(self.root, self.session, typing.text)
                self.wake(self.session, {})
                typing.text, typing.cursor = "", 0
                self.follow = True
            else:
                runtime.validate_text(typing.text)
                self.store(self.session).edit(typing.target, typing.text)
                self.compose()
            self.next_inbox = float("-inf")
            self.refresh_now.set()
            return
        super().field_key(name)

    def session_controls(self):
        current = self.runtimes.get(self.session, {})
        state = current.get("state")
        controls = []
        if state == "handed-off":
            controls.append(console.Control("r", "Return", lambda: self.runtime_action("resume")))
        elif state in ("stopped", "failed"):
            controls.append(console.Control("r", "Resume", lambda: self.runtime_action("resume")))
        else:
            controls += [console.Control("i", "Interrupt", lambda: self.runtime_action("interrupt")),
                         console.Control("x", "Stop", lambda: self.runtime_action("stop"))]
        if state != "handed-off":
            controls.append(console.Control("o", "Native", lambda: self.runtime_action("handoff")))
        commands = self.runtime_views.get(self.session, {}).get("commands", [])
        checking = any(command["state"] == "uncertain" for command in commands)
        if checking:
            controls.append(console.Control("k", "Check", self.show_messages))
        if any(command["state"] == "queued" for command in commands):
            controls.append(console.Control("q", "Queue", self.show_messages))
        if current.get("paused") and not checking and state not in ("handed-off", "stopped", "failed"):
            controls.append(console.Control("s", "Continue", lambda: self.runtime_action("send")))
        if not self.follow:
            controls.append(console.Control("f", "Latest", self.follow_latest))
        controls += [console.Control("b", "Inbox", self.show_inbox),
                     console.Control("p", "Details", lambda: self.session_mode("chat-info")),
                     console.Control("h", "Sessions", self.go_home)]
        return controls

    def follow_latest(self):
        self.follow = True
        self.compose()

    def session_mode(self, purpose):
        self.menu_page = 0
        self.field = InlineField(purpose, None, "")

    def runtime_action(self, action):
        if action == "handoff" and self.runtimes[self.session].get("state") == "handed-off":
            raise UsageError("Close the native terminal and return to Projector first")
        runtime.control(self.root, self.session, action)
        if action == "handoff":
            self.pending_native = self.session
        self.ensure()
        self.compose()
        self.refresh_now.set()

    def show_inbox(self):
        self.field = None
        entries = [(key, entry) for key, entry in self.entries() if not key.startswith(MESSAGE)]
        if entries:
            self.open_drawer(entries[0][0])
        else:
            self.compose()
            self.status = "No inbox items or pull requests"

    def show_messages(self):
        self.field, self.queue_open = None, True
        messages = [(key, entry) for key, entry in self.entries() if key.startswith(MESSAGE)]
        if messages:
            key, _ = next((pair for pair in messages if pair[1].command["state"] == "uncertain"), messages[0])
            self.open_drawer(key)
        else:
            self.compose()

    def controls(self):
        pending = dict(self.entries()).get(self.drawer)
        if not isinstance(pending, PendingMessage):
            return super().controls()
        command = pending.command
        ident = command["id"]
        if command["state"] == "queued":
            return [console.Control("e", "Edit message", lambda: self.edit_message(ident), True, True),
                    console.Control("d", "Remove message", lambda: self.remove_message(ident), True),
                    console.Control("0", "Back to chat", self.compose)]
        state = self.runtimes[self.session].get("state")
        if state in ("handed-off", "stopped", "failed"):
            return [console.Control("r", "Return to Projector" if state == "handed-off" else "Resume Projector",
                                    lambda: self.runtime_action("resume"), True, True),
                    console.Control("o", "Open agent conversation", lambda: self.runtime_action("handoff"), True)]
        if self.recovery_retry == ident:
            return [console.Control("0", "Cancel", self.cancel_retry, True, True),
                    console.Control("s", "Send this message again", lambda: self.resolve_message(ident, True), True)]
        return [console.Control("o", "Open agent conversation", lambda: self.runtime_action("handoff"), True, True),
                console.Control("a", "Already reached the agent", lambda: self.resolve_message(ident, False), True),
                console.Control("s", "Send again", lambda: self.confirm_retry(ident), True)]

    def confirm_retry(self, ident):
        self.recovery_retry, self.control = ident, "0"

    def cancel_retry(self):
        self.recovery_retry, self.control = None, "s"

    def resolve_message(self, ident, retry):
        command = self.store(self.session).command(ident)
        if command is None or command["state"] != "uncertain":
            raise UsageError("This message no longer needs checking")
        self.runtime_action(("retry:" if retry else "confirm:") + ident)

    def editable_message(self, ident):
        command = self.store(self.session).command(ident)
        if command is None or command["state"] != "queued":
            raise UsageError("This message is already being handled")
        source = command["source"]
        if source:
            item = inbox.find(self.session, source[1]) if source[:1] == ["inbox"] else {}
            if item.get("kind") != "message" or item.get("pr"):
                raise UsageError("Use this item's inbox or pull request controls")
        return command

    def edit_message(self, ident):
        command = self.editable_message(ident)
        self.close_drawer()
        self.queue_open, self.selected = False, CONVERSATION
        self.field = InlineField("queue-edit", ident, "Edit message: ", command["text"], len(command["text"]))

    def remove_message(self, ident):
        command = self.editable_message(ident)
        self.store(self.session).edit(ident, None)
        source = command["source"]
        if source[:1] == ["inbox"]:
            with contextlib.suppress(ProjectorError):
                inbox.settle(self.session, source[1], source[3], delivered=False)
        self.compose()
        self.refresh_now.set()

    def launch_key(self, name):
        typing = self.field
        purpose = typing.purpose
        if name == "ctrl-c" or (name == "esc" and purpose == "launch-prompt"):
            self.field = self.launch_prompt = None
        elif name == "esc":
            self.field = self.launch_prompt
        elif purpose == "launch-policy":
            if name in ("enter", "down"):
                self.field = self.launch_prompt
        elif purpose == "launch-prompt":
            if name == "up":
                self.menu_page = 0
                self.field = InlineField("launch-settings", None, "")
            elif name == "enter":
                runtime.launch(self.root, self.launch_form["backend"], self.launch_form["cwd"], typing.text,
                    name=self.launch_form["name"], model=self.launch_form["model"],
                    parent=os.environ.get("PROJECTOR_SESSION", ""))
                self.ensure()
                self.field = self.launch_prompt = None
                self.next_inbox = float("-inf")
                self.refresh_now.set()
            else:
                super().field_key(name)
        elif purpose == "launch-settings":
            if name in ("c", "x"):
                self.menu_page = 0
                backend = "claude" if name == "c" else "codex"
                if backend != self.launch_form["backend"]:
                    self.launch_form["backend"], self.launch_form["model"] = backend, ""
                self.field = InlineField("launch-models", None, "")
            elif name == "m":
                self.menu_page = 0
                self.field = InlineField("launch-models", None, "")
            elif name == "p":
                self.field = InlineField("launch-policy", None, "")
            elif name in ("d", "n"):
                field, prompt = ("cwd", "Directory: ") if name == "d" else ("name", "Name (optional): ")
                value = self.launch_form[field]
                self.field = InlineField("launch-" + field, None, prompt, value, len(value))
            elif name in ("enter", "down"):
                self.field = self.launch_prompt
        elif purpose == "launch-models":
            choices = self.model_choices()
            if name in choices:
                self.launch_form["model"] = choices[name][1]
                self.field = self.launch_prompt
            elif name == "t":
                value = self.launch_form["model"]
                self.field = InlineField("launch-model", None, "Model: ", value, len(value))
            elif name in ("enter", "down"):
                self.field = self.launch_prompt
        elif name == "enter":
            field = purpose.removeprefix("launch-")
            value = typing.text if field == "cwd" else typing.text.strip()
            if field == "cwd":
                directory = Path(value).expanduser().resolve()
                if not value or not directory.is_dir():
                    raise UsageError("Choose an existing working directory")
                value = str(directory)
            elif field == "model" and value and not agents.MODEL_ID.fullmatch(value):
                raise UsageError("Use a native model ID without spaces or control characters")
            self.launch_form[field] = value
            self.field = self.launch_prompt
        else:
            super().field_key(name)

    def model_choices(self):
        backend = self.launch_form["backend"]
        choices = {"0": ("Native default", "")}
        if backend == "claude":
            choices.update({"o": ("Opus", "opus"), "s": ("Sonnet", "sonnet")})
        known = {value for _, value in choices.values()}
        models = [model for model in self.launch_models.get(backend, []) if model not in known]
        choices.update({key: (model + " (from your sessions)", model)
                        for key, model in zip("123456789", models)})
        return choices

    def footer(self, width=None):
        width = max(1, width or 100)
        typing = self.field
        if typing is None:
            if self.view == "home":
                if self.status:
                    return [Line([(console.fit(self.status, width), "error")])]
                hint = "n new agent session"
                if self.sessions:
                    session = self.viewed()
                    action = "Enter expand" if session and session.directory in self.collapsed else (
                        "Enter open · Backspace collapse")
                    hint += " · ↑↓ select · " + action
                return [Line([(console.fit(hint + " · Ctrl-C quit", width), "subtle")])]
            return super().footer(width)
        purpose = typing.purpose
        if self.status:
            return [Line([(console.fit(self.status, width), "error")])]
        if purpose == "launch-settings":
            options = [("c", "Claude"), ("x", "Codex"), ("m", "model"), ("d", "directory"),
                       ("n", "name"), ("p", "permissions"), ("down", "prompt")]
            return [self.menu_line([console.Control(key, label,
                    lambda key=key: self.launch_option(purpose, key)) for key, label in options], width)]
        elif purpose == "launch-models":
            options = [(key, label.split(" (")[0]) for key, (label, _) in self.model_choices().items()]
            options += [("t", "other"), ("down", "prompt")]
            return [self.menu_line([console.Control(key, label,
                    lambda key=key: self.launch_option(purpose, key)) for key, label in options], width)]
        elif purpose == "launch-policy":
            text = "Native permissions and sandbox settings apply, including configured automatic approvals. ↓ prompt"
        elif purpose in ("launch-cwd", "launch-name", "launch-model"):
            return [self.inline_field(typing, width)]
        elif purpose == "chat-options":
            return [self.menu_line(self.session_controls(), width)]
        elif purpose == "chat-info":
            current = self.runtimes.get(self.session, {})
            policy = current.get("policy", {})
            text = (f"{current.get('backend', 'Agent')} · {current.get('state', 'unknown')} · "
                    f"approvals: {policy.get('approval') or 'native settings'} · "
                    f"sandbox: {policy.get('sandbox') or 'native settings'} · ↓ prompt")
        elif purpose in ("launch-prompt", "chat", "queue-edit"):
            current = self.runtimes.get(self.session, {})
            commands = self.runtime_views.get(self.session, {}).get("commands", [])
            if purpose == "chat" and any(command["state"] == "uncertain" for command in commands):
                return [Line([("Check message delivery · ↑ then k", "warning")])]
            if purpose == "chat" and current.get("state") == "handed-off":
                text = "Native terminal has control · ↑ then r to return"
            elif purpose == "chat" and current.get("paused"):
                text = "Messages paused · ↑ then s to continue"
            else:
                text = "↑ options · Enter send · Shift-Enter or Ctrl-J newline"
        else:
            return super().footer(width)
        return [Line([(console.fit(text, width), "subtle")])]

    def launch_option(self, purpose, key):
        if self.field is not None and self.field.purpose == purpose:
            self.field_key(key)

    def menu_line(self, controls, width):
        available = max(1, width - 6)
        pages, current, used = [], [], 0
        for control in controls:
            key = "↓" if control.key == "down" else control.key
            text = console.fit(f"{key} {control.label}", available).rstrip()
            if current and used + 2 + len(text) > available:
                pages.append(current)
                current, used = [], 0
            current.append((text, control))
            used += len(text) + (2 if len(current) > 1 else 0)
        pages.append(current)
        self.menu_page %= len(pages)
        parts, targets, start = [], [], 0
        for text, control in pages[self.menu_page]:
            if parts:
                parts.append(("  ", ""))
                start += 2
            parts.append((text, "accent"))
            targets.append((start, start + len(text), console.Target(self.session, None, control)))
            start += len(text)
        if len(pages) > 1:
            parts.append(("  ← →", "subtle"))
        return Line(parts, targets=targets)

    @staticmethod
    def inline_field(typing, width):
        prompt = typing.prompt[:max(1, width // 3)]
        space = max(1, width - len(prompt) - 1)
        start = max(0, typing.cursor - space)
        return Line([(prompt, "bold"), (typing.text[start:start + space], "")],
                    cursor=min(width - 1, len(prompt) + typing.cursor - start))

    def item_controls(self, item):
        controls = super().item_controls(item)
        if item["kind"] == "question" and not item.get("answer"):
            controls.insert(len(item.get("choices", [])), console.Control("t", "Type an answer",
                lambda: self.open_field("typed-answer", item["ref"], "Answer: ")))
            pending = self.runtime_views.get(self.session, {}).get("pending_requests", {})
            if item["ref"] in pending:
                controls = [control for control in controls if control.key not in ("6", "7", "9")]
        return controls

    def item_line(self, item, now, selected):
        if isinstance(item, PendingMessage):
            label = "Check message" if item.command["state"] == "uncertain" else "Waiting message"
            preview = item.command["text"].replace("\n", " ")[:160]
            return Line([(f"- {label}: {preview}", "warning")], selected)
        return super().item_line(item, now, selected)

    def item_drawer(self, item, width):
        if isinstance(item, PendingMessage):
            command = item.command
            indent = " " * console.DRAWER_INDENT
            wrap = max(1, width - len(indent))
            lines = []
            if command["state"] == "uncertain":
                explanation = ("Projector lost contact before it could record what happened to this message. "
                               "Check the agent's conversation. Other messages will continue after you check these.")
                if self.recovery_retry == command["id"]:
                    explanation = "Sending this message again can repeat file edits or commands. Send it again?"
                lines.extend(Line([(indent + part, "warning")]) for part in textwrap.wrap(explanation, wrap))
            for paragraph in command["text"].split("\n"):
                lines.extend(Line([(indent + part, "")]) for part in textwrap.wrap(paragraph, wrap) or [""])
            lines.append(Line([]))
            lines.extend(self.option_line(control) for control in self.controls())
            return lines
        lines = super().item_drawer(item, width)
        if item["ref"] in self.runtime_views.get(self.session, {}).get("pending_requests", {}):
            lines.append(Line([("Native request: answer, type a clarification, or dismiss to deny/cancel.", "subtle")]))
        if self.field is not None and self.field.purpose == "typed-answer" and self.field.target == item["ref"]:
            lines.append(self.form_line(self.field))
        return lines

    def send(self, typing):
        if typing.purpose != "typed-answer":
            return super().send(typing)
        item = self.item("item:" + typing.target)
        if item is None:
            raise UsageError("This question is no longer visible")
        answer = self.record(lambda session: inbox.answer(session, typing.target, inbox.typed_letter(item),
                            text=typing.text, subject=item["subject"], choices=item["choices"]))
        if answer is not None:
            self.field = None

    def lines(self, width):
        with self.lock:
            if self.field is not None and self.field.purpose.startswith("launch-"):
                return self.launch_lines(width)
            if self.view == "panel" and self.session in self.runtimes:
                return self.panel_lines(width)
            return super().lines(width)

    def viewport(self, width, room):
        with self.lock:
            if self.field is not None and self.field.purpose.startswith("launch-"):
                lines, (_, last) = self.launch_lines(width)
                start = max(0, last - room + 1)
                return lines[start:start + room]
            if self.view == "panel" and self.session in self.runtimes:
                return self.panel_viewport(width, room)
            return super().viewport(width, room)

    def launch_lines(self, width):
        width = max(1, width)
        form = self.launch_form
        summary = f"{form['backend']} · {form['model'] or 'Native default'}"
        if form["name"]:
            summary += " · " + form["name"]
        lines = [Line([("New agent session", "bold")]),
                 Line([(console.fit(summary, width), "subtle")]),
                 Line([(console.fit("Directory: " + form["cwd"], width), "subtle")]), Line([])]
        prompt = self.field_lines(self.launch_prompt, width, active=self.field is self.launch_prompt)
        focus = len(lines) + next(at for at, line in enumerate(prompt) if line.spot)
        return [*lines, *prompt], (focus, focus)

    @staticmethod
    def field_lines(typing, width, *, active=True):
        text = typing.prompt + typing.text
        cursor = len(typing.prompt) + typing.cursor
        lines, offset = [], 0
        for paragraph in text.split("\n"):
            parts = [paragraph[at:at + width] for at in range(0, len(paragraph), width)] or [""]
            row, column = divmod(cursor - offset, width)
            here = offset <= cursor <= offset + len(paragraph)
            if here and row == len(parts):
                parts.append("")
            lines.extend(Line([(part, "")], spot=here and row == at,
                              cursor=column if active and here and row == at else None) for at, part in enumerate(parts))
            offset += len(paragraph) + 1
        return lines

    @staticmethod
    def form_line(typing):
        text = typing.text.replace("\n", " ↵ ")
        cursor = len(typing.prompt) + len(typing.text[:typing.cursor].replace("\n", " ↵ "))
        return Line([(typing.prompt, "accent"), (text, "")], spot=True, cursor=cursor)

    def runtime_lines(self, name, width, selected):
        lines = []
        current = self.runtimes[name]
        if current.get("error"):
            lines.append(Line([(current["error"], "error")]))
        view = self.runtime_views.get(name, {})
        result = view.get("control_result", {})
        if result.get("error"):
            lines.append(Line([(result["error"], "error")]))
        for who, text in self.chats.get(name, []):
            lines.append(Line([]))
            for paragraph in text.split("\n"):
                for part in textwrap.wrap(paragraph, max(1, width - 2)) or [""]:
                    if who == "you":
                        lines.append(Line([((" " + part).ljust(width), "user")]))
                    else:
                        lines.append(Line([(part, "subtle" if who == "tool" else "warning" if who == "status" else "")]))
        lines.append(Line([]))
        composing = self.field is not None and self.field.purpose in ("chat", "chat-options", "chat-info", "queue-edit")
        draft = self.field if composing and self.field.purpose == "queue-edit" else self.drafts.get(name)
        if draft is not None:
            parts = self.field_lines(draft, max(1, width), active=self.field is draft)
            if not composing:
                for line in parts:
                    line.spot = False
            lines.extend(parts)
        else:
            lines.append(Line([(self.message_prompt(name), "bold")]))
        return lines

    def message_prompt(self, name):
        state = self.runtimes.get(name, {}).get("state")
        return "… " if state in ("starting", "running", "awaiting-user") else "❯ "


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
                    self.input_mode(False)
                    self.curses.def_prog_mode()
                    self.curses.endwin()
                    try:
                        native_handoff.wait(command)
                    finally:
                        self.curses.reset_prog_mode()
                        self.window.refresh()
                        self.input_mode(True)
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
