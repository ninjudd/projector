from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import runtime, watch
from .core import EnvironmentError


def wait(command: list[str], **options) -> int:
    process = subprocess.Popen(command, **options)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # The foreground native CLI handles Ctrl-C; keep its lease until it exits.
            continue


def run(root: Path, session: str) -> int:
    folder = watch.session_folder(root, session)
    with runtime.worker_lock(folder, "runtime-native.lock") as descriptor:
        if descriptor is None:
            raise EnvironmentError("This session already has a native terminal")
        current = runtime.record(folder)
        if current.get("owner") != "native":
            raise EnvironmentError("Wait for the managed worker to release this session")
        env = dict(os.environ)
        for key in ("PROJECTOR_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
            env.pop(key, None)
        env["PROJECTOR_SESSION"] = session
        env["PROJECTOR_PARENT_SESSION"] = current.get("parent", "")
        # The child keeps the lease even if this wrapper exits before it does.
        return wait(runtime.native_command(current), cwd=current["cwd"], env=env, pass_fds=(descriptor,))


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), sys.argv[2]))
