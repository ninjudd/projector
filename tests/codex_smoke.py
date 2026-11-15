"""Opt-in live queue test with a disposable local server, thread, and hooks.

Requires native Codex and Node 22+. No installed plugin/configuration is changed,
and no GitHub mutation is made. Use --review-hooks to trust the four temporary
test hooks in Codex's native hook browser before the test runs.
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
from projector.inbox import locked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hook", type=Path)
    parser.add_argument("--review-hooks", action="store_true")
    args = parser.parse_args()
    if args.hook:
        event = json.load(sys.stdin)
        print(json.dumps(codex_watch.hook(args.hook, event, time.time(), lambda: None)))
        return
    executable, node = shutil.which("codex"), shutil.which("node")
    if not executable or not node:
        raise SystemExit("Install native Codex and Node 22+ before running this live test")
    repo = Path(__file__).resolve().parents[1]
    thread = None
    server = None
    with tempfile.TemporaryDirectory(prefix="projector-codex-smoke-") as temporary:
        root = Path(temporary).resolve()
        script = root / "hook.py"
        script.write_text("import sys\nsys.path.insert(0, " + repr(str(repo / "src")) + ")\n"
                          "sys.path.insert(0, " + repr(str(repo / "tests")) + ")\n"
                          "from codex_smoke import main\nmain()\n")
        words = [sys.executable, str(script), "--hook", str(root / "watch")]
        command = subprocess.list2cmdline(words) if os.name == "nt" else shlex.join(words)
        group = '[{hooks=[{type="command",command=' + json.dumps(command) + '}]}]'
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        endpoint = f"ws://127.0.0.1:{port}"
        argv = [executable, "--sandbox", "read-only"]
        argv.extend(["app-server", "--listen", endpoint])
        for name in ("SessionStart", "UserPromptSubmit", "SessionEnd", "Stop"):
            argv.extend(["-c", "hooks." + name + "=" + group])

        def start(log):
            process = subprocess.Popen(argv, stdout=log, stderr=log)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        return process
                except OSError:
                    time.sleep(0.1)
            process.terminate()
            process.wait(timeout=15)
            raise RuntimeError("isolated Codex server did not start")

        def stop(process):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)

        with (root / "server.log").open("w") as log:
            try:
                server = start(log)
                if args.review_hooks:
                    print("Open /hooks, review the four temporary test hooks, then /exit to run the test.", flush=True)
                    subprocess.run([executable, "--remote", endpoint, "--cd", str(root)], check=True)
                result = subprocess.run([node, str(repo / "tests" / "codex_smoke.mjs"), endpoint, str(root)],
                                        capture_output=True, text=True, timeout=90)
                for line in result.stdout.splitlines():
                    thread = json.loads(line).get("thread", thread)
                if result.returncode:
                    raise RuntimeError("initial Codex turn failed: " + result.stderr[-1000:])
                if thread is None:
                    raise RuntimeError("Codex did not create a test thread")
                stop(server)
                server = start(log)
                folder = watch.session_folder(root / "watch", thread)
                if not codex_watch.registration(folder):
                    raise RuntimeError("test hooks did not run; review hook trust")
                codex_watch.register(folder, thread, str(root), time.time())
                watch.add(root / "watch", thread, watch.Subscription("acme/transport-fixture", 14, "fix"))
                notice = codex_watch.Event("NOTICE transport smoke test: reply only ACK; no PR actions.",
                                          ["acme/transport-fixture", 14, "fix", "smoke"])
                watch.append_events(folder, [notice])
                with locked(folder):
                    batch = codex_watch.prepare(folder, time.time())
                def run(command, **kwargs):
                    return subprocess.run([*command, "--remote", endpoint], **kwargs)
                dispatcher = codex_watch.Dispatcher(run=run)
                try:
                    dispatcher.send(folder, batch["batch_id"])
                finally:
                    dispatcher.close()
                receipt = folder / "codex-receipts" / f"{batch['batch_id']}.json"
                deadline = time.monotonic() + 45
                while not receipt.exists() and time.monotonic() < deadline:
                    time.sleep(0.2)
                if not receipt.exists():
                    raise RuntimeError("queued thread did not acknowledge: " + json.dumps(codex_watch.report(folder, time.time())))
                print("PASS: an unloaded Codex thread resumed and acknowledged its native queued batch")
            finally:
                if server and server.poll() is None:
                    try:
                        if thread:
                            subprocess.run([executable, "archive", thread, "--remote", endpoint],
                                           capture_output=True, timeout=15)
                    finally:
                        stop(server)


if __name__ == "__main__":
    main()
