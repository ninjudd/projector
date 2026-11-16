"""Opt-in live test of Codex delivery across idle unloads, with a disposable local server, thread, and hooks.

The server unloads an idle thread after a few seconds instead of Codex's 30 minutes, so the test sees the
same `SessionEnd` a real idle conversation sends. It then delivers a batch, checks that the thread wakes,
acknowledges it, and finishes the turn, does that once more after a second unload, and finally checks
that an archived conversation is disconnected rather than resumed.

Requires native Codex and Node 22+. No installed plugin/configuration is changed, and no GitHub mutation
is made. Use --review-hooks once per checkout to trust the four test hooks in Codex's native hook browser;
their command stays the same between runs, so the trust carries over.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from projector import codex_watch, watch

HOOKS = ("SessionStart", "UserPromptSubmit", "SessionEnd", "Stop")
UNLOAD = 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", action="store_true")
    parser.add_argument("--review-hooks", action="store_true")
    args = parser.parse_args()
    if args.hook:
        hook()
        return
    executable, node = shutil.which("codex"), shutil.which("node")
    if not executable or not node:
        raise SystemExit("Install native Codex and Node 22+ before running this live test")
    with tempfile.TemporaryDirectory(prefix="projector-codex-smoke-") as temporary:
        Smoke(executable, node, Path(temporary).resolve()).run(args.review_hooks)


def hook() -> None:
    root = Path(os.environ["PROJECTOR_CODEX_SMOKE"])
    event = json.load(sys.stdin)
    with (root / "hooks.jsonl").open("a") as stream:
        stream.write(json.dumps({"at": time.time(), "event": event.get("hook_event_name"),
                                 "transcript": event.get("transcript_path")}) + "\n")
    print(json.dumps(codex_watch.hook(root / "watch", event, time.time(), lambda: None)))


class Smoke:
    def __init__(self, executable: str, node: str, root: Path) -> None:
        self.executable, self.node, self.root = executable, node, root
        self.repo = Path(__file__).resolve().parents[1]
        self.server: subprocess.Popen | None = None
        self.thread: str | None = None
        self.calls: list[list[str]] = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.endpoint = f"ws://127.0.0.1:{self.port}"

    def run(self, review: bool) -> None:
        with (self.root / "server.log").open("w") as log:
            try:
                self.start(log)
                if review:
                    print("Open /hooks, review the four test hooks, then /exit to run the test.", flush=True)
                    subprocess.run([self.executable, "--remote", self.endpoint, "--cd", str(self.root)], check=True)
                self.first_turn()
                folder = watch.session_folder(self.root / "watch", self.thread)
                if not codex_watch.registration(folder):
                    raise RuntimeError("test hooks did not run; review hook trust")
                watch.add(self.root / "watch", self.thread, watch.Subscription("acme/transport-fixture", 14, "fix"))
                for attempt in (1, 2):
                    self.wait_for("SessionEnd", attempt, "Codex did not unload the idle thread")
                    if not codex_watch.active(folder, time.time()):
                        raise RuntimeError("an idle unload disconnected delivery: "
                                           + json.dumps(codex_watch.report(folder, time.time())))
                    batch = self.deliver(folder, attempt)
                    receipt = folder / "codex-receipts" / f"{batch['batch_id']}.json"
                    self.wait(receipt.exists, 60, "the woken thread did not acknowledge batch "
                              + json.dumps(codex_watch.report(folder, time.time())))
                    self.wait_for("Stop", attempt + 1, "the woken thread did not finish its turn")
                    print(f"PASS {attempt}: an idle-unloaded thread woke, acknowledged, and finished its turn")
                self.archive()
                self.deliver(folder, 3, expect_call=False)
                report = codex_watch.report(folder, time.time())
                if report["state"] != "disconnected" or "archived" not in report["last_error"]:
                    raise RuntimeError("an archived conversation was not disconnected: " + json.dumps(report))
                print("PASS: an archived conversation is disconnected instead of resumed")
            finally:
                self.stop()

    def start(self, log) -> None:
        words = [sys.executable, str(Path(__file__).resolve()), "--hook"]
        command = subprocess.list2cmdline(words) if os.name == "nt" else shlex.join(words)
        group = '[{hooks=[{type="command",command=' + json.dumps(command) + '}]}]'
        argv = [self.executable, "--sandbox", "read-only", "app-server", "--listen", self.endpoint,
                "-c", f"thread_unload_delay_secs={UNLOAD}"]
        for name in HOOKS:
            argv.extend(["-c", "hooks." + name + "=" + group])
        environment = dict(os.environ, PROJECTOR_CODEX_SMOKE=str(self.root),
                           PYTHONPATH=os.pathsep.join([str(self.repo / "src"), str(self.repo / "tests")]))
        self.server = subprocess.Popen(argv, stdout=log, stderr=log, env=environment)
        self.wait(self.listening, 15, "isolated Codex server did not start")

    def listening(self) -> bool:
        if self.server.poll() is not None:
            raise RuntimeError("isolated Codex server exited; see its log")
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                return True
        except OSError:
            return False

    def first_turn(self) -> None:
        # The client disconnects once the turn completes, so no client holds the thread open.
        result = subprocess.run([self.node, str(self.repo / "tests" / "codex_smoke.mjs"), self.endpoint,
                                 str(self.root)], capture_output=True, text=True, timeout=90)
        for line in result.stdout.splitlines():
            self.thread = json.loads(line).get("thread", self.thread)
        if result.returncode:
            raise RuntimeError("initial Codex turn failed: " + result.stderr[-1000:])
        if self.thread is None:
            raise RuntimeError("Codex did not create a test thread")

    def deliver(self, folder: Path, attempt: int, *, expect_call: bool = True) -> dict:
        notice = codex_watch.Event(f"NOTICE transport smoke test {attempt}: reply only ACK; no PR actions.",
                                   ["acme/transport-fixture", 14, "fix", "smoke", attempt])
        watch.append_events(folder, [notice])
        before = len(self.calls)
        dispatcher = codex_watch.Dispatcher(run=self.queue)
        try:
            dispatcher.tick([folder])
            for job in list(dispatcher.jobs.values()):
                job.result()
        finally:
            dispatcher.close()
        if (len(self.calls) > before) != expect_call:
            raise RuntimeError(f"batch {attempt} {'was not' if expect_call else 'was'} queued: "
                               + json.dumps(codex_watch.report(folder, time.time())))
        found = codex_watch.pending(folder)
        return found[0][1] if found else {}

    def queue(self, command, **kwargs):
        self.calls.append(command)
        return subprocess.run([*command, "--remote", self.endpoint], **kwargs)

    def archive(self) -> None:
        subprocess.run([self.executable, "archive", self.thread, "--remote", self.endpoint],
                       capture_output=True, timeout=15, check=True)
        self.thread = None

    def hooks(self, name: str) -> list[dict]:
        path = self.root / "hooks.jsonl"
        lines = path.read_text().splitlines() if path.exists() else []
        return [entry for entry in map(json.loads, lines) if entry["event"] == name]

    def wait_for(self, name: str, count: int, failure: str) -> None:
        self.wait(lambda: len(self.hooks(name)) >= count, UNLOAD + 60, failure)

    @staticmethod
    def wait(condition, seconds: float, failure: str) -> None:
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                raise RuntimeError(failure)
            time.sleep(0.2)

    def stop(self) -> None:
        if self.server is None or self.server.poll() is not None:
            return
        try:
            if self.thread:
                subprocess.run([self.executable, "archive", self.thread, "--remote", self.endpoint],
                               capture_output=True, timeout=15)
        finally:
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=15)


if __name__ == "__main__":
    main()
