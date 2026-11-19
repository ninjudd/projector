"""The OS keychain that holds the relay's private key, through tools each system ships.

- **macOS** runs `security`: `security -i` reads the store command on standard input, and
  `find-generic-password` reads the item back.
- **Windows** calls Credential Manager's `CredWriteW`, `CredReadW`, and `CredDeleteW` in `advapi32` through
  `ctypes`, with a generic credential.
- **Linux** runs `secret-tool`, which reads the secret to store from standard input.

Each item is stored under a service name and an account, its key ID: `projector-agent` for the relay's key, and
another service name for another secret, such as a hosted service's account token.

No backend passes the key as an argument, where the process list would show it. Every call is bounded by a
timeout, because a locked keychain may not answer: `security` shows an unlock dialog and waits for a person, so a
process that runs past the timeout is killed, and a Windows call runs on a worker thread that is no longer waited
for.
"""

from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from .core import ProjectorError, run_bounded

SERVICE = "projector-agent"
SECURITY = "/usr/bin/security"
SECRET_TOOL = "secret-tool"
TIMEOUT = 5.0
SYSTEM = sys.platform
NOT_ANSWERING = "keychain locked or not answering"
# What `security -i` reads as more than a word of its script.
UNSCRIPTABLE = re.compile(r"[\s\"'`\\\x00-\x1f\x7f]")


class KeychainError(ProjectorError):
    """A keychain that is missing, refuses, or does not answer in time."""


def failure(tool: str, done: subprocess.CompletedProcess, secret: str = "") -> KeychainError:
    lines = done.stderr.decode("utf-8", "replace").strip().splitlines()
    reason = lines[-1] if lines else f"exited with status {done.returncode}"
    if secret:
        reason = reason.replace(secret, "<redacted>")
    return KeychainError(f"{tool}: {reason}")


def run(tool: str, args: list[str], timeout: float, *, input: Optional[bytes] = None,
        secret: str = "") -> bytes:
    try:
        done = run_bounded(args, timeout=timeout, input=input)
    except subprocess.TimeoutExpired:
        raise KeychainError(f"{tool}: {NOT_ANSWERING}") from None
    except OSError:
        raise KeychainError(f"{tool}: not installed") from None
    if done.returncode != 0:
        raise failure(tool, done, secret)
    return done.stdout


class MacOS:
    name = "macOS Keychain"

    def usable(self) -> str:
        return "" if Path(SECURITY).is_file() else "security: not installed"

    def store(self, key_id: str, secret: str, timeout: float, service: str = SERVICE) -> None:
        # The secret goes in the script `security -i` reads on standard input, never on its command line, where
        # the process list would show it. That script splits on whitespace and reads quotes and backslashes, so a
        # value holding one could end the command and start another; the relay key's base64 holds none.
        for name, value in (("service name", service), ("account", key_id), ("secret", secret)):
            if not value or UNSCRIPTABLE.search(value):
                raise KeychainError(f"security: the {name} holds whitespace, a quote, a backslash, or a control "
                                    "character, which the keychain's script cannot carry, so nothing was stored")
        command = f"add-generic-password -U -s {service} -a {key_id} -w {secret}\n"
        run("security", [SECURITY, "-i"], timeout, input=command.encode(), secret=secret)

    def read(self, key_id: str, timeout: float, service: str = SERVICE) -> str:
        found = run("security", [SECURITY, "find-generic-password", "-s", service, "-a", key_id, "-w"], timeout)
        return found.decode("utf-8", "replace").strip()

    def delete(self, key_id: str, timeout: float, service: str = SERVICE) -> None:
        run("security", [SECURITY, "delete-generic-password", "-s", service, "-a", key_id], timeout)


class Linux:
    name = "Secret Service"

    def usable(self) -> str:
        return "" if shutil.which(SECRET_TOOL) else "secret-tool: not installed"

    def store(self, key_id: str, secret: str, timeout: float, service: str = SERVICE) -> None:
        label = f"Projector agent key {key_id}" if service == SERVICE else f"{service} {key_id}"
        run("secret-tool", [SECRET_TOOL, "store", f"--label={label}", "service", service, "key", key_id], timeout,
            input=secret.encode(), secret=secret)

    def read(self, key_id: str, timeout: float, service: str = SERVICE) -> str:
        found = run("secret-tool", [SECRET_TOOL, "lookup", "service", service, "key", key_id], timeout)
        return found.decode("utf-8", "replace").strip()

    def delete(self, key_id: str, timeout: float, service: str = SERVICE) -> None:
        run("secret-tool", [SECRET_TOOL, "clear", "service", service, "key", key_id], timeout)


class FILETIME(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p),
                ("Comment", ctypes.c_wchar_p), ("LastWritten", FILETIME), ("CredentialBlobSize", ctypes.c_uint32),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", ctypes.c_uint32),
                ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def load_advapi32() -> object:
    return ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]


def last_error() -> int:
    return getattr(ctypes, "get_last_error", lambda: 0)()


def bounded(call: Callable[[], object], timeout: float) -> object:
    """`call()` on a worker thread, given up on after `timeout` seconds."""
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as error:
            outcome["error"] = error

    worker = threading.Thread(target=target, name="keychain", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise KeychainError(f"Credential Manager: {NOT_ANSWERING}")
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


class Windows:
    name = "Windows Credential Manager"

    def __init__(self) -> None:
        self.api: Optional[object] = None

    def library(self):
        if self.api is None:
            self.api = load_advapi32()
        return self.api

    def usable(self) -> str:
        try:
            self.library()
        except (OSError, AttributeError):
            return "Credential Manager: advapi32 is not available"
        return ""

    @staticmethod
    def target(key_id: str, service: str = SERVICE) -> str:
        return f"{service}:{key_id}"

    def store(self, key_id: str, secret: str, timeout: float, service: str = SERVICE) -> None:
        def write() -> None:
            data = secret.encode("utf-8")
            blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            credential = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=self.target(key_id, service),
                                    CredentialBlobSize=len(data),
                                    CredentialBlob=ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)),
                                    Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=key_id)
            if not self.library().CredWriteW(ctypes.pointer(credential), 0):
                raise KeychainError(f"Credential Manager: CredWriteW failed with error {last_error()}")
        bounded(write, timeout)

    def read(self, key_id: str, timeout: float, service: str = SERVICE) -> str:
        def lookup() -> str:
            found = ctypes.POINTER(CREDENTIAL)()
            api = self.library()
            if not api.CredReadW(self.target(key_id, service), CRED_TYPE_GENERIC, 0, ctypes.pointer(found)):
                raise KeychainError(f"Credential Manager: CredReadW failed with error {last_error()}")
            try:
                credential = found.contents
                return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode("utf-8")
            finally:
                api.CredFree(found)
        return str(bounded(lookup, timeout))

    def delete(self, key_id: str, timeout: float, service: str = SERVICE) -> None:
        def remove() -> None:
            if not self.library().CredDeleteW(self.target(key_id, service), CRED_TYPE_GENERIC, 0):
                raise KeychainError(f"Credential Manager: CredDeleteW failed with error {last_error()}")
        bounded(remove, timeout)


def backend():
    """This system's backend: macOS, Windows, or Linux and every other POSIX system."""
    if SYSTEM == "darwin":
        return MacOS()
    if SYSTEM == "win32":
        return Windows()
    return Linux()


def store(key_id: str, secret: str, timeout: Optional[float] = None, *, service: str = SERVICE) -> str:
    """Store `secret` under `key_id` in `service` and read it back, and answer the keychain's name. A backend
    counts as usable only when both succeed in time."""
    chosen = backend()
    missing = chosen.usable()
    if missing:
        raise KeychainError(missing)
    timeout = TIMEOUT if timeout is None else timeout
    chosen.store(key_id, secret, timeout, service)
    if chosen.read(key_id, timeout, service) != secret:
        raise KeychainError(f"{chosen.name}: the stored key did not read back")
    return chosen.name


def usable() -> str:
    """Why this system's keychain cannot be used, or "" when its tool or library is there."""
    return backend().usable()


def read(key_id: str, timeout: Optional[float] = None, *, service: str = SERVICE) -> str:
    chosen = backend()
    missing = chosen.usable()
    if missing:
        raise KeychainError(missing)
    found = chosen.read(key_id, TIMEOUT if timeout is None else timeout, service)
    if not found:
        raise KeychainError(f"{chosen.name}: holds no key {key_id}")
    return found


def delete(key_id: str, timeout: Optional[float] = None, *, service: str = SERVICE) -> None:
    backend().delete(key_id, TIMEOUT if timeout is None else timeout, service)
