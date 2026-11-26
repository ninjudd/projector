from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from . import agents, codex_watch, inbox, runtime, runtime_backends, watch
from .core import EnvironmentError, ProjectorError
from .inbox import locked, read_json, write_json
from .runtime_store import Store


class Worker:
    def __init__(self, root: Path, launch: dict, factory=runtime_backends.create) -> None:
        self.root, self.launch, self.factory = root, launch, factory
        self.generation = str(uuid.uuid4())
        self.session = launch.get("session")
        self.backend = None
        self.current = None
        self.requests: set[str] = set()
        self.paused = False
        self.interrupted = False
        self.state = "starting"
        self.last_heartbeat = 0.0
        self.control_seen = None
        self.store = None
        self.stopping = False
        self.text = ""
        self.text_item = None
        self.last_flush = time.monotonic()

    def snapshot(self, *, owner="managed", error="") -> None:
        with locked(self.folder):
            write_json(self.folder / "runtime.json", {
                "schema_version": 1, "session": self.session, "backend": self.launch["backend"],
                "cwd": self.launch["cwd"], "name": self.launch.get("name", ""),
                "generation": self.generation, "pid": os.getpid(), "launch": self.launch["id"],
                "parent": self.launch.get("parent", ""),
                "executable": self.launch.get("executable"), "backend_version": self.launch.get("backend_version"),
                "sdk": runtime.SDK if self.launch["backend"] == "claude" else None,
                "owner": owner, "state": self.state, "renewed_at": time.time(), "error": error,
                "policy": getattr(self.backend, "policy", {}), "paused": self.paused})
            write_json(self.folder / "session.json", {"deliverer": "managed", "renewed_at": watch.stamp(time.time())})

    async def connect(self) -> str:
        options = {**self.launch, "session": self.session}
        self.backend = self.factory(options, self.request)
        try:
            return await self.backend.open()
        except BaseException:
            await self.backend.close()
            self.backend = None
            raise

    async def run(self) -> None:
        if self.session:
            self.folder = watch.session_folder(self.root, self.session)
            with runtime.worker_lock(self.folder) as held:
                if not held:
                    raise EnvironmentError("Another worker owns this session")
                await self.run_owned()
        else:
            self.session = await self.connect()
            self.folder = watch.session_folder(self.root, self.session)
            with runtime.worker_lock(self.folder) as held:
                if not held:
                    await self.backend.close()
                    raise EnvironmentError("Another worker owns this session")
                await self.run_owned()

    async def run_owned(self) -> None:
        try:
            self.store = Store(self.folder)
            self.control_seen = self.store.value("control_seen")
            self.store.recover()
            for ref in self.store.value("requests", []):
                with contextlib.suppress(ProjectorError):
                    inbox.dismiss(self.session, ref, withdrawn=True)
            self.store.set(requests=[])
            self.paused = bool(self.store.commands(("uncertain",)))
            self.state = "recovering" if self.paused else "ready"
            with codex_watch.submission(self.folder) as held:
                if not held:
                    raise EnvironmentError("Native event delivery is still submitting; try resume again")
                self.snapshot()
            if self.backend is None:
                await self.connect()
            self.launch.update(session=self.session, state="live")
            launch_folder = self.root / "launches" / self.launch["id"]
            with locked(launch_folder):
                write_json(launch_folder / "launch.json", self.launch)
            env = {**os.environ, "PROJECTOR_SESSION": self.session,
                   ("CLAUDE_CODE_SESSION_ID" if self.launch["backend"] == "claude" else "CODEX_THREAD_ID"): self.session}
            agents.register(self.root, self.session, name=self.launch.get("name") or None,
                summary="Managed agent", model=self.launch.get("model") or None,
                cwd=Path(self.launch["cwd"]), now=time.time(), environ=env)
            initial = str(uuid.uuid5(uuid.NAMESPACE_URL, "projector-launch:" + self.launch["id"]))
            self.store.enqueue(initial, self.launch["prompt"], [])
            while not self.stopping:
                await self.tick()
        except BaseException as error:
            self.state = "failed"
            self.snapshot(error=str(error))
            raise
        finally:
            if self.store is not None:
                self.flush()
            if self.backend is not None:
                await self.backend.close()
            if self.store is not None:
                self.store.recover()
            for ref in list(self.requests):
                with contextlib.suppress(ProjectorError):
                    inbox.dismiss(self.session, ref, withdrawn=True)
            self.requests.clear()
            if self.store is not None:
                self.store.set(requests=[])
            if self.state != "failed":
                self.state = "stopped"
                self.snapshot()

    def flush(self) -> None:
        if self.text:
            self.store.event("text", {"text": self.text, "item": self.text_item}, self.current)
            self.text = ""
        self.last_flush = time.monotonic()

    def ingest(self) -> None:
        if codex_watch.unsettled(self.folder):
            codex_watch.settle(self.folder, codex_watch.unsettled(self.folder))
        for command in self.store.commands(("queued", "submitting", "accepted", "uncertain")):
            source = command["source"]
            if source[:1] == ["inbox"]:
                claim = codex_watch.claim_of(self.session, source[1])
                if inbox.now_ms() - claim.get("renewedAt", 0) >= codex_watch.RENEW_AFTER_MS:
                    with contextlib.suppress(ProjectorError):
                        inbox.claim(self.session, source[1], renew=source[3])
        with locked(self.folder):
            codex_watch.offer(self.folder, exclude=self.requests)
            waiting = codex_watch.queued(self.folder)
            for event in waiting:
                source = json.loads(event["key"])
                text = event["line"]
                if source[:1] == ["inbox"]:
                    item = inbox.find(self.session, source[1])
                    if item["kind"] == "message" and not item.get("pr"):
                        index = inbox.discussed_message(item)
                        if index is not None:
                            text = item["messages"][index]["text"]
                ident = str(uuid.uuid5(uuid.NAMESPACE_URL, "projector-event:" + event["key"]))
                self.store.enqueue(ident, text, source)
            if waiting:
                codex_watch.save_queue(self.folder, [])

    def accepted(self, turn=None) -> None:
        if self.current and self.store.accepted(self.current, self.generation, turn):
            self.settle(self.current)

    def settle(self, ident: str) -> None:
        command = self.store.command(ident)
        if command and command["source"][:1] == ["inbox"]:
            codex_watch.settle(self.folder, [command["source"]])

    async def tick(self) -> None:
        self.ingest()
        control = self.store.value("control")
        if control and control["id"] != self.control_seen:
            self.control_seen = control["id"]
            self.store.set(control_seen=self.control_seen)
            try:
                await self.act(control["action"])
                self.store.set(control_result={"id": control["id"], "error": ""})
            except ProjectorError as error:
                self.store.set(control_result={"id": control["id"], "error": str(error)})
        if time.time() - self.last_heartbeat >= 20:
            self.last_heartbeat = time.time()
            self.snapshot(owner="native" if self.state == "handed-off" else "managed")
            from .cli import distribution_version, newer_release
            watch.ensure_daemon(self.root, distribution_version(), newer_release)
        if self.stopping:
            return
        if self.backend is None:
            await asyncio.sleep(0.2)
            return
        if self.current is None and not self.paused:
            commands = self.store.commands(("queued",))
            if commands:
                command = commands[0]
                source = command["source"]
                if source and not codex_watch.eligible(self.folder, {"key": json.dumps(source), "line": command["text"]}):
                    self.store.edit(command["id"], None)
                    return
                submitted = self.store.begin(command["id"], self.generation)
                if submitted:
                    self.current = command["id"]
                    self.interrupted = False
                    self.state = "running"
                    self.snapshot()
                    turn = await self.backend.submit(submitted["text"], command["id"])
                    if turn:
                        self.accepted(turn)
        try:
            event = await asyncio.wait_for(self.backend.events.get(), 0.2)
        except TimeoutError:
            if self.text and time.monotonic() - self.last_flush >= 0.25:
                self.flush()
            return
        kind = event["type"]
        if event.get("turn") and self.current:
            current = self.store.command(self.current)
            if current.get("turn") and event["turn"] != current["turn"]:
                return
        if kind == "accepted":
            self.accepted(event.get("turn"))
        elif kind == "configuration":
            self.snapshot()
        elif kind == "text":
            self.accepted()
            if self.text and self.text_item != event.get("item"):
                self.flush()
            self.text_item = event.get("item")
            self.text += event.get("text", "")
            if len(self.text.encode()) >= 16 * 1024:
                self.flush()
        elif kind == "turn_end" and self.current:
            self.accepted(event.get("turn"))
            self.flush()
            outcome = "interrupted" if self.interrupted else event["outcome"]
            self.store.finish(self.current, self.generation, outcome, event.get("error"))
            self.current = None
            if outcome != "completed":
                self.paused = True
            self.state = "ready"
            self.snapshot()
        elif kind == "disconnected":
            raise EnvironmentError(event.get("error", "Agent disconnected"))
        elif kind in ("tool", "usage"):
            self.flush()
            self.store.event(kind, event, self.current)

    async def act(self, action: str) -> None:
        if action == "interrupt":
            if self.backend is None:
                raise EnvironmentError("Return from the native terminal first")
            self.paused = self.interrupted = True
            await self.backend.interrupt()
        elif action == "stop":
            if self.state == "handed-off":
                raise EnvironmentError("Close the native terminal and return before stopping")
            self.stopping = True
        elif action == "send":
            if self.store.commands(("uncertain",)):
                raise EnvironmentError("Resolve uncertain input before sending queued work")
            self.paused = False
        elif action.startswith("confirm:"):
            ident = action.removeprefix("confirm:")
            self.store.resolve(ident, retry=False)
            self.settle(ident)
        elif action == "handoff":
            if self.current or self.requests:
                raise EnvironmentError("Interrupt the turn and wait for it to end before opening the native terminal")
            if self.backend is None:
                raise EnvironmentError("The session is already handed off")
            await self.backend.close()
            self.backend = None
            self.state = "handed-off"
            self.paused = True
            self.snapshot(owner="native")
        elif action == "resume":
            if self.backend is not None or self.state != "handed-off":
                raise EnvironmentError("The session already has a managed controller")
            with runtime.worker_lock(self.folder, "runtime-native.lock") as held:
                if not held:
                    raise EnvironmentError("The native terminal is still open; close it before returning")
                await self.connect()
                self.state = "ready"
                self.paused = True
                self.snapshot()
        else:
            raise EnvironmentError("Unknown runtime control")

    async def ask(self, title: str, detail: str, options: list[str]) -> str:
        choices = options[:4] or ["Continue"]
        item = inbox.ask(self.session, " ".join(title.split())[:120], choices[0], choices[1:], detail=detail)
        ref = item["ref"]
        self.requests.add(ref)
        self.store.set(requests=sorted(self.requests))
        self.state = "awaiting-user"
        self.snapshot()
        command = self.current
        generation = self.generation
        try:
            while True:
                if self.interrupted or self.stopping or command != self.current or generation != self.generation:
                    raise asyncio.CancelledError
                item = inbox.find(self.session, ref)
                answer = item.get("answer")
                if answer:
                    claim = inbox.claim(self.session, ref)
                    inbox.settle(self.session, ref, claim["claim"]["token"], delivered=True)
                    return "" if answer.get("dismissed") else answer.get("text", "")
                await asyncio.sleep(0.1)
        finally:
            self.requests.discard(ref)
            self.store.set(requests=sorted(self.requests))
            with contextlib.suppress(ProjectorError):
                if not inbox.find(self.session, ref).get("answer"):
                    inbox.dismiss(self.session, ref, withdrawn=True)
            self.state = "running" if self.current else "ready"
            self.snapshot()

    async def request(self, method: str, params: dict) -> dict:
        detail = json.dumps(params, ensure_ascii=False, indent=2)
        if method in ("claude/approval", "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                      "applyPatchApproval", "execCommandApproval", "item/permissions/requestApproval"):
            answer = await self.ask("Allow this agent action?", f"Runtime request: `{method}`\n\n```json\n{detail}\n```",
                                    ["Deny", "Allow once"])
            allow = answer == "Allow once"
            if method == "item/permissions/requestApproval":
                return {"permissions": params.get("permissions", {}) if allow else {}, "scope": "turn"}
            return {"decision": "accept" if allow else "decline"}
        if method in ("claude/question", "item/tool/requestUserInput"):
            answers = {}
            for question in params.get("questions", []):
                title = question.get("question") or question.get("header") or "The agent needs your input"
                options = [one["label"] for one in question.get("options", [])]
                answer = await self.ask(title, title, options)
                key = question["question"] if method == "claude/question" else question["id"]
                answers[key] = answer if method == "claude/question" else {"answers": [answer]}
            return {"answers": answers}
        if method == "mcpServer/elicitation/request":
            answer = await self.ask(params.get("message", "MCP server needs input"),
                                    detail + "\n\nType a JSON object, or Cancel.", ["Cancel"])
            if not answer or answer == "Cancel":
                return {"action": "cancel", "content": None, "_meta": None}
            import jsonschema
            value = json.loads(answer)
            jsonschema.validate(value, params.get("requestedSchema", {"type": "object"}))
            return {"action": "accept", "content": value, "_meta": None}
        raise EnvironmentError(f"Unsupported native request: {method}")


async def serve(root: Path, ident: str) -> None:
    folder = root / "launches" / ident
    with runtime.worker_lock(folder) as held:
        if not held:
            return
        launch = read_json(folder / "launch.json")
        if not launch:
            return
        try:
            await Worker(root, launch).run()
        except BaseException as error:
            with locked(folder):
                current = read_json(folder / "launch.json") or launch
                write_json(folder / "launch.json", {**current, "state": "failed", "error": str(error)})
            raise


if __name__ == "__main__":
    asyncio.run(serve(Path(sys.argv[1]), sys.argv[2]))
