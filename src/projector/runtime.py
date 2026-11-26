from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import venv
from pathlib import Path

from .core import EnvironmentError, UsageError
from .inbox import locked, read_json, try_lock, unlock, write_json
from .runtime_store import Store

SDK = "claude-agent-sdk==0.2.189"
LEASE = 90
MINIMUM = {"claude": (2, 1, 332), "codex": (0, 172, 0)}


def record(folder: Path) -> dict:
    return read_json(folder / "runtime.json") or {}


def managed(folder: Path) -> bool:
    return (folder / "runtime.sqlite").exists() or record(folder).get("owner") in ("managed", "native")


def live(folder: Path, now: float) -> bool:
    current = record(folder)
    return (current.get("owner") in ("managed", "native") and now - current.get("renewed_at", 0) < LEASE
            and current.get("state") not in ("failed", "stopped"))


@contextlib.contextmanager
def worker_lock(folder: Path, filename: str = "runtime-worker.lock"):
    folder.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(folder / filename, os.O_CREAT | os.O_RDWR, 0o600)
    held = try_lock(descriptor)
    try:
        yield descriptor if held else None
    finally:
        if held:
            unlock(descriptor)
        os.close(descriptor)


def environment_key() -> str:
    from .core import distribution_version
    digest = hashlib.sha256((SDK + distribution_version() + str(sys.version_info[:2])).encode())
    package = Path(__file__).parent
    for path in sorted(package.rglob("*.py")):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


def environment(root: Path) -> Path:
    return root.parent / "runtimes" / environment_key()


def setup(root: Path, *, run=subprocess.run) -> Path:
    target = environment(root)
    with locked(target):
        if not (target / "ready.json").exists():
            venv.EnvBuilder(with_pip=True).create(target)
            python = target / "bin" / "python"
            result = run([str(python), "-m", "pip", "install", SDK], check=False)
            if result.returncode:
                raise EnvironmentError("Agent dependency installation failed; run `projector setup` again")
            package = target / "app" / "projector"
            shutil.copytree(Path(__file__).parent, package, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            from .site import ASSETS
            if ASSETS.is_dir():
                shutil.copytree(ASSETS, package / "site" / "assets", dirs_exist_ok=True)
            with contextlib.suppress(importlib.metadata.PackageNotFoundError):
                distribution = importlib.metadata.distribution("projector-cli")
                metadata = target / "app" / f"projector_cli-{distribution.version}.dist-info"
                metadata.mkdir(exist_ok=True)
                (metadata / "METADATA").write_text(str(distribution.metadata), encoding="utf-8")
            write_json(target / "ready.json", {"sdk": SDK, "key": target.name})
    return target


def launch(root: Path, backend: str, cwd: str, prompt: str, *, name: str = "", model: str = "",
           parent: str = "") -> str:
    from . import agents
    if backend not in ("claude", "codex"):
        raise UsageError("Choose claude or codex")
    if name:
        name = name.lower()
        if not agents.NAME.fullmatch(name):
            raise UsageError("Use a name of 1 to 32 letters, digits, and hyphens, starting with a letter or digit")
        if any(value.get("name") == name and agents.live(root, session, time.time())
               for session, value in agents.records(root).items()):
            raise UsageError("An active local session already uses that name; choose another")
    if model and not agents.MODEL_ID.fullmatch(model):
        raise UsageError("Use a native model ID without spaces or control characters")
    directory = Path(cwd).expanduser().resolve()
    if not directory.is_dir():
        raise UsageError("Choose an existing working directory")
    executable = shutil.which(backend)
    if executable is None:
        raise EnvironmentError(f"Install {backend} and sign in with its native CLI first")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise EnvironmentError(f"Cannot start {backend}: {error}") from error
    if version.returncode:
        raise EnvironmentError(f"{backend} --version failed; repair its native installation first")
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version.stdout)
    if match is None or tuple(map(int, match.groups())) < MINIMUM[backend]:
        minimum = ".".join(map(str, MINIMUM[backend]))
        raise EnvironmentError(f"Managed {backend} needs version {minimum} or newer; update the native CLI first")
    validate_text(prompt)
    target = environment(root)
    if not (target / "ready.json").exists():
        raise EnvironmentError("Run `projector setup` to install the optional agent environment")
    ident = str(uuid.uuid4())
    folder = root / "launches" / ident
    with locked(folder):
        write_json(folder / "launch.json", {"id": ident, "backend": backend, "cwd": str(directory),
            "prompt": prompt, "name": name, "model": model, "parent": parent,
            "executable": executable, "backend_version": version.stdout.strip()[:512] or "unknown",
            "environment": str(target), "state": "queued", "created_at": time.time()})
    return ident


def validate_text(text: str) -> None:
    if not text.strip() or "\0" in text or len(text.encode("utf-8")) > 64 * 1024:
        raise UsageError("Use a nonempty prompt of at most 64 KiB, without NUL characters")


def launches(root: Path) -> list[dict]:
    return [value for path in sorted((root / "launches").glob("*/launch.json"))
            if (value := read_json(path))]


def coordinate(root: Path) -> None:
    for item in launches(root):
        folder = root / "launches" / item["id"]
        if item.get("state") != "queued":
            continue
        with locked(folder):
            item = read_json(folder / "launch.json") or {}
            if item.get("state") != "queued":
                continue
            target = Path(item["environment"])
            executable = target / "bin" / "python"
            try:
                log = (folder / "worker.log").open("ab")
                with log:
                    env = {**os.environ, "PYTHONPATH": str(target / "app"),
                           "PROJECTOR_PARENT_SESSION": item.get("parent", "")}
                    process = subprocess.Popen([str(executable), "-m", "projector.runtime_worker", str(root), item["id"]],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, start_new_session=True,
                        cwd=item["cwd"])
                item.update(state="starting", pid=process.pid, spawned_at=time.time())
            except OSError as error:
                item.update(state="failed", error=str(error))
            write_json(folder / "launch.json", item)


def change_launch(root: Path, ident: str, *, retry: bool) -> None:
    folder = root / "launches" / ident
    with worker_lock(folder) as acquired:
        if acquired is None:
            raise UsageError("The launch worker is still starting; wait for it to register")
        with locked(folder):
            current = read_json(folder / "launch.json")
            if not current or current.get("session"):
                raise UsageError("Use the registered session's controls")
            write_json(folder / "launch.json", {**current, "state": "queued" if retry else "cancelled", "error": ""})


def sessions(root: Path) -> dict[str, dict]:
    from . import watch
    found = {}
    for name in watch.session_names(root):
        current = record(watch.session_folder(root, name))
        if not current and (watch.session_folder(root, name) / "runtime.sqlite").exists():
            launch = next((item for item in launches(root) if item.get("session") == name), {})
            current = {"session": name, "state": "failed", "owner": "managed", "backend": launch.get("backend", "agent"),
                       "cwd": launch.get("cwd", ""), "name": launch.get("name", ""),
                       "error": "Session metadata is unreadable; execution state is preserved"}
        if current:
            if current.get("owner") == "managed" and not live(watch.session_folder(root, name), time.time()) \
                    and current.get("state") not in ("failed", "stopped"):
                current = {**current, "state": "failed", "error": "Worker disconnected; resume to inspect recovery"}
            found[name] = current
    return found


def send(root: Path, session: str, text: str) -> dict:
    from . import inbox, watch
    validate_text(text)
    if not record(watch.session_folder(root, session)):
        raise UsageError("This session is not managed by Projector")
    return inbox.post(session, "message", "About this session", detail="", pr=None, agent=None,
                      messages=[{"text": text, "at": inbox.now_ms()}])


def control(root: Path, session: str, action: str) -> None:
    from . import watch
    folder = watch.session_folder(root, session)
    if not record(folder):
        raise UsageError("This session is not managed by Projector")
    with worker_lock(folder) as available:
        if available is None:
            Store(folder).request_control(str(uuid.uuid4()), action)
            return
        if action == "handoff":
            with locked(folder):
                current = record(folder)
                write_json(folder / "runtime.json", {**current, "owner": "native", "state": "handed-off"})
        elif action == "resume":
            with worker_lock(folder, "runtime-native.lock") as native_available:
                if native_available is None:
                    raise UsageError("Close the native terminal before returning")
                with locked(folder):
                    current = record(folder)
                    write_json(folder / "runtime.json", {**current, "owner": "managed", "state": "starting"})
                restart(root, session, held=True)
        else:
            raise UsageError("The worker is stopped; press r to resume it")


def restart(root: Path, session: str, *, held: bool = False) -> None:
    from . import watch
    folder = watch.session_folder(root, session)
    context = contextlib.nullcontext(True) if held else worker_lock(folder)
    with context as acquired:
        if not acquired:
            raise UsageError("The worker is still running")
        current = record(folder)
        origin = root / "launches" / current["launch"]
        with locked(origin):
            launch = read_json(origin / "launch.json")
            if launch is None:
                raise EnvironmentError("The session's launch record is missing")
            launch.update(state="queued", session=session)
            write_json(origin / "launch.json", launch)


def native_command(current: dict) -> list[str]:
    executable = current.get("executable") or current["backend"]
    if current["backend"] == "claude":
        return [executable, "--resume", current["session"]]
    return [executable, "--no-daemon", "resume", current["session"]]
