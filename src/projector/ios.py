"""`project ios`: build a branch's iOS app in a worktree Projector keeps, put it on a device, and bring the
device's crash reports and log back. `docs/projects/ios/readme.md` is the plan this follows."""

from __future__ import annotations

import collections
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote

from . import review
from .core import EnvironmentError, ProjectorError, UsageError

TOOLS_MESSAGE = "project ios needs Xcode's command-line tools on macOS"
DEVICE_DESTINATION = "generic/platform=iOS"
MAC_DESTINATION = "platform=macOS,arch=arm64,variant=Designed for iPad"
APPLICATION = "com.apple.product-type.application"
SKIPPED = frozenset({".git", "Pods", "node_modules", "build", ".build"})
BUNDLES = (".xcworkspace", ".xcodeproj")
RECORD = "build.json"
NUMBER = re.compile(r"[0-9]+")
SPAN = re.compile(r"[1-9][0-9]*[smhd]")
# A log entry starts with its date; `log show` adds a header and a footer that do not.
LOG_ENTRY = re.compile(r"^[0-9]{4}-")
WINDOW = 10
SHOWN_ERRORS = 20
TAIL = 30
CRASHES = 3
FRAMES = 20
LAUNCH_RETRY = 2
LAUNCH_WAIT = 300
QUIT_WAIT = 30
GRAY, PLAIN = "\033[90m", "\033[0m"


class IosError(ProjectorError):
    exit_code = 1


class BuildFailed(IosError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.exit_code = status


def require_tools() -> None:
    if not (shutil.which("xcrun") and shutil.which("xcodebuild")):
        raise EnvironmentError(TOOLS_MESSAGE)


def tool(args: list[str]) -> str:
    """Run one of Apple's tools, `cp`, or `open`, and return its output, or fail with what it printed."""
    result = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        raise IosError(f"{' '.join(args[:3])} failed: {detail[-1] if detail else f'exit {result.returncode}'}")
    return result.stdout


# State

@dataclass(frozen=True)
class State:
    folder: Path

    @property
    def lock(self) -> Path:
        return self.folder / "lock"

    @property
    def worktree(self) -> Path:
        return self.folder / "worktree"

    @property
    def derived(self) -> Path:
        return self.folder / "DerivedData"

    @property
    def log(self) -> Path:
        return self.folder / "build.log"

    @property
    def builds(self) -> Path:
        return self.folder / "builds"

    @property
    def mac(self) -> Path:
        return self.folder / "mac"


def state_for(repo: str) -> State:
    return State(review.state_home() / "ios" / repo)


@contextmanager
def locked(state: State, what: str) -> Iterator[None]:
    """Hold the repository's lock while the block runs, waiting for a run that holds it. The kernel drops
    a `flock` when its holder exits, however it exits, so a crash leaves nothing to clear."""
    import fcntl

    state.folder.mkdir(parents=True, exist_ok=True)
    with open(state.lock, "a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            holder = handle.read().strip() or "another run"
            print(f"Waiting for {holder} to finish…", file=sys.stderr, flush=True)
            fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        handle.truncate()
        handle.write(f"process {os.getpid()}, project ios {what}")
        handle.flush()
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()


# Devices

@dataclass(frozen=True)
class Device:
    name: str
    udid: str
    kind: str
    tunnel: str

    @property
    def installable(self) -> bool:
        return self.tunnel != "unavailable"

    @property
    def label(self) -> str:
        return f"{self.name} ({self.udid}, {self.tunnel})"

    def as_json(self) -> dict:
        return {"name": self.name, "udid": self.udid, "kind": self.kind, "tunnel": self.tunnel,
                "installable": self.installable}


def paired_devices() -> list[Device]:
    with tempfile.TemporaryDirectory() as folder:
        listing = Path(folder) / "devices.json"
        tool(["xcrun", "devicectl", "list", "devices", "--json-output", str(listing)])
        found = json.loads(listing.read_text())["result"]["devices"]
    devices = []
    for entry in found:
        hardware = entry.get("hardwareProperties") or {}
        connection = entry.get("connectionProperties") or {}
        if (hardware.get("reality"), hardware.get("platform"), connection.get("pairingState")) != \
                ("physical", "iOS", "paired"):
            continue
        devices.append(Device((entry.get("deviceProperties") or {}).get("name", "?"), hardware.get("udid", ""),
                              hardware.get("deviceType", "device"), connection.get("tunnelState", "")))
    return devices


def reach(device: str) -> str:
    return (f"Unlock {device}, plug it into this Mac and tap Trust if it asks, or open Xcode's "
            "Window > Devices and Simulators with it plugged in.")


def choose_device(devices: list[Device], wanted: Optional[str], kind: Optional[str]) -> Device:
    """The device `--device` names, or the one connected device of `kind`, or of any kind, preferring a
    connected tunnel to one that is merely not `unavailable`."""
    paired = "Paired devices:\n  " + ("\n  ".join(device.label for device in devices) or "none")
    if wanted:
        matches = [device for device in devices if wanted in (device.udid, device.name)]
        if not matches:
            raise IosError(f"no paired device is named {wanted}. {paired}")
        if len(matches) > 1:
            raise IosError(f"more than one paired device is named {wanted}; pass its UDID. {paired}")
        if not matches[0].installable:
            raise IosError(f"{matches[0].label} can't be reached. {reach('it')}")
        return matches[0]
    what = kind or "device"
    candidates = [device for device in devices if not kind or device.kind == kind]
    matches = ([device for device in candidates if device.tunnel == "connected"]
               or [device for device in candidates if device.installable])
    if not matches:
        raise IosError(f"no {what} is connected. {reach('your ' + what)}\n{paired}")
    if len(matches) > 1:
        raise IosError(f"more than one {what} is connected; pass --device with a name or UDID. {paired}")
    return matches[0]


# The head

@dataclass(frozen=True)
class Target:
    text: str
    key: str
    ref: str
    branch: str
    sha: Optional[str]


def target_key(text: str) -> str:
    """The folder a target's kept build lives in. Percent-encoding keeps every branch name one folder
    deep and apart from every other."""
    return f"pr-{int(text)}" if NUMBER.fullmatch(text) else "branch-" + quote(text, safe="")


def resolve_target(repo: str, text: str, allow_untrusted: bool) -> Target:
    """The ref to fetch for a branch or a pull request. A pull request whose head is not trusted is
    refused before anything is fetched, because `xcodebuild` runs the project's build scripts as the
    person."""
    if not NUMBER.fullmatch(text):
        return Target(text, target_key(text), f"refs/heads/{text}", text, None)
    number = int(text)
    pr = review.read_pull_request(repo, number)
    trusted, why = review.trusted_head(repo, pr, review.authenticated_login())
    if not trusted and not allow_untrusted:
        raise ProjectorError(f"{repo}#{number} is not trusted to build: {why}. xcodebuild runs its build "
                             "phases' scripts as you, so read them, then pass --allow-untrusted to build it")
    return Target(text, target_key(text), review.head_ref(repo, pr), pr["head"]["ref"], pr["head"]["sha"])


def fetched(checkout: Path, target: Target) -> str:
    sha = review.fetch(checkout, target.ref)
    if target.sha and sha != target.sha:
        raise IosError(f"#{target.text}'s head moved to {sha[:7]} while it was fetched; run the command again")
    return sha


def place_worktree(checkout: Path, state: State, target: Target) -> str:
    """Put the worktree at the target's head and return the commit. A worktree belongs to the clone that
    made it, which may be another clone of the repository, so the fetch and checkout run through the
    worktree itself while its clone exists."""
    worktree = state.worktree
    if worktree.exists():
        try:
            review.git("-C", str(worktree), "rev-parse", "--git-common-dir")
        except review.ReviewError:
            shutil.rmtree(worktree)
        else:
            sha = fetched(worktree, target)
            review.git("-C", str(worktree), "checkout", "--quiet", "--force", "--detach", sha)
            return sha
    review.git("-C", str(checkout), "worktree", "prune")
    sha = fetched(checkout, target)
    review.add_worktree(checkout, worktree, sha)
    return sha


def remove_worktree(worktree: Path) -> None:
    try:
        common = review.git("-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir")
        review.git("--git-dir", common, "worktree", "remove", "--force", str(worktree))
    except review.ReviewError:
        shutil.rmtree(worktree, ignore_errors=True)


# The app

@dataclass(frozen=True)
class Product:
    name: str
    bundle_id: str
    path: Path


def find_project(worktree: Path, configured: Optional[str]) -> Path:
    if configured:
        path = worktree / configured
        if path.suffix not in BUNDLES or not path.is_dir():
            raise IosError(f"ios.project is {configured}, which is no Xcode project or workspace in the worktree")
        return path
    found: dict[str, list[Path]] = {suffix: [] for suffix in BUNDLES}
    for folder, names, _ in os.walk(worktree):
        kept = []
        for name in sorted(names):
            suffix = Path(name).suffix
            if suffix in found:
                found[suffix].append(Path(folder) / name)
            elif name not in SKIPPED:
                kept.append(name)
        names[:] = kept
    for suffix in BUNDLES:
        paths = found[suffix]
        if len(paths) == 1:
            return paths[0]
        if paths:
            listed = ", ".join(str(path.relative_to(worktree)) for path in paths)
            raise IosError(f"the worktree has more than one {suffix}: {listed}; choose one with "
                           "`project config set ios.project <path>`")
    raise IosError("the worktree has no .xcworkspace or .xcodeproj; name one with "
                   "`project config set ios.project <path>`")


def project_args(project: Path) -> list[str]:
    return ["-workspace" if project.suffix == ".xcworkspace" else "-project", str(project)]


def choose_scheme(project: Path, app: str) -> str:
    listing = json.loads(tool(["xcodebuild", "-list", "-json", *project_args(project)]))
    schemes = (listing.get("workspace") or listing.get("project") or {}).get("schemes") or []
    for scheme in (f"{app} Development", app):
        if scheme in schemes:
            return scheme
    raise IosError(f"{project.name} has no scheme named {app} Development or {app}; its schemes are "
                   f"{', '.join(schemes) or 'none'}. Pass one as --app")


def read_product(project: Path, scheme: str, destination: str, derived: Path) -> Product:
    entries = json.loads(tool(["xcodebuild", "-showBuildSettings", "-json", *project_args(project),
                               "-scheme", scheme, "-destination", destination, "-derivedDataPath", str(derived)]))
    for entry in entries:
        settings = entry.get("buildSettings") or {}
        if settings.get("PRODUCT_TYPE") == APPLICATION:
            name = settings["FULL_PRODUCT_NAME"]
            return Product(name, settings["PRODUCT_BUNDLE_IDENTIFIER"], Path(settings["TARGET_BUILD_DIR"]) / name)
    raise IosError(f"the scheme {scheme} builds no app")


# The build

def took(start: float) -> str:
    seconds = int(time.monotonic() - start)
    return f"{seconds // 60}m {seconds % 60:02d}s"


def build_errors(lines: list[str]) -> list[str]:
    errors: list[str] = []
    for line in lines:
        is_error = " error: " in line or line.startswith("error: ")
        if is_error and "failed with exit code 0" not in line and line not in errors:
            errors.append(line)
    return errors[:SHOWN_ERRORS] or lines[-TAIL:]


def run_build(command: list[str], log_path: Path, verbose: bool) -> None:
    """Run the build with every line in `log_path`. A terminal sees the last lines scroll by in gray; any
    other reader, such as an agent's shell, sees only the result, unless `verbose` passes every line."""
    window = sys.stdout.isatty() and not verbose
    recent: collections.deque[str] = collections.deque(maxlen=WINDOW)
    shown = 0
    drawn = 0.0

    def draw() -> None:
        nonlocal shown
        width = max(20, shutil.get_terminal_size().columns - 1)
        frame = f"\033[{shown}F" if shown else ""
        for line in recent:
            frame += f"\033[2K{GRAY}{line[:width]}{PLAIN}\n"
        shown = len(recent)
        sys.stdout.write(frame)
        sys.stdout.flush()

    def clear() -> None:
        if shown:
            sys.stdout.write(f"\033[{shown}F\033[J")
            sys.stdout.flush()

    start = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, errors="replace")
        assert process.stdout is not None
        try:
            for line in process.stdout:
                log.write(line)
                if verbose:
                    sys.stdout.write(line)
                elif window:
                    recent.append(line.rstrip("\n").expandtabs(4))
                    if time.monotonic() - drawn > 0.05:
                        draw()
                        drawn = time.monotonic()
            status = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            clear()
            raise
    clear()
    gray, plain = (GRAY, PLAIN) if window else ("", "")
    if status == 0:
        print(f"{gray}Built in {took(start)}. Log: {log_path}{plain}", flush=True)
        return
    lines = log_path.read_text(errors="replace").splitlines(keepends=True)
    sys.stdout.flush()
    sys.stderr.write("".join(build_errors(lines)))
    raise BuildFailed(f"the build failed after {took(start)}; its whole log is {log_path}", status)


# Kept builds

@dataclass(frozen=True)
class Kept:
    folder: Path
    record: dict

    @property
    def app(self) -> str:
        return self.record["app"]

    @property
    def app_path(self) -> Path:
        return self.folder / self.record["product"]

    @property
    def product(self) -> str:
        return self.record["product"].removesuffix(".app")

    @property
    def described(self) -> str:
        return f"{self.app} from {self.record['target']} at {self.record['sha'][:7]}"


def copy_app(source: Path, destination: Path) -> None:
    """Copy an app folder. On a Mac `cp -c` makes an APFS clone, which costs no time or space until one
    copy changes; other systems copy every file."""
    if sys.platform == "darwin":
        tool(["cp", "-cR", str(source), str(destination)])
    else:
        shutil.copytree(source, destination, symlinks=True)


def keep(state: State, app: str, scheme: str, target: Target, sha: str, product: Product, mac: bool) -> Kept:
    folder = state.builds / quote(app, safe=" ") / target.key
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    copy_app(product.path, folder / product.name)
    record = {"app": app, "scheme": scheme, "target": target.text, "branch": target.branch, "sha": sha,
              "destination": "mac" if mac else "device", "product": product.name, "bundle_id": product.bundle_id,
              "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (folder / RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return Kept(folder, record)


def kept_builds(state: State) -> list[Kept]:
    """Every kept build, oldest first."""
    builds = []
    for path in state.builds.glob(f"*/*/{RECORD}"):
        try:
            builds.append(Kept(path.parent, json.loads(path.read_text())))
        except (OSError, ValueError):
            continue
    return sorted(builds, key=lambda kept: kept.record.get("built_at", ""))


def kept_target(state: State, app: Optional[str], target: Optional[str]) -> Kept:
    """The newest kept build of `target`, or with none the newest of all, of `app` when it is given."""
    key = target_key(target) if target else None
    builds = [kept for kept in kept_builds(state)
              if (app is None or kept.app == app) and (key is None or kept.folder.name == key)]
    if not builds:
        of = f" of {target}" if target else ""
        command = f"project ios build {target or '<branch-or-number>'}" + (f" --app '{app}'" if app else "")
        raise IosError(f"no kept build{of}; run `{command}` to make one")
    return builds[-1]


def kept_app(state: State, app: Optional[str]) -> Kept:
    """The newest kept build of `app`, or of the one app with kept builds, which names the product and bundle
    ID that crash reports and log lines carry."""
    builds = kept_builds(state)
    if app is None:
        apps = sorted({kept.app for kept in builds})
        if len(apps) > 1:
            raise IosError(f"there are kept builds of {', '.join(apps)}; pass --app with one of them")
    builds = [kept for kept in builds if app is None or kept.app == app]
    if not builds:
        of = f" of {app}" if app else ""
        raise IosError(f"no kept build{of} names the app; run `project ios build <branch-or-number>` first")
    return builds[-1]


# Install and open

def install_on(device: Device, kept: Kept) -> None:
    """Install the kept build and open it. A locked device takes the install but refuses the launch, as it
    often is by the time a build ends, so the launch waits for the unlock."""
    tool(["xcrun", "devicectl", "device", "install", "app", "--quiet", "--device", device.udid,
          str(kept.app_path)])
    print(f"Installed {kept.described} on {device.name}.", flush=True)
    waiting: Optional[float] = None
    while True:
        result = subprocess.run(["xcrun", "devicectl", "device", "process", "launch", "--quiet", "--device",
                                 device.udid, "--terminate-existing", kept.record["bundle_id"]],
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode == 0:
            print("Opened it.")
            return
        output = (result.stderr.strip() or result.stdout.strip())
        if "locked" not in output.lower():
            raise IosError(f"{kept.app} is installed, but it could not be opened: {output}")
        if waiting is None:
            print(f"Unlock your {device.kind} to open it. Waiting…", flush=True)
            waiting = time.monotonic()
        elif time.monotonic() - waiting >= LAUNCH_WAIT:
            raise IosError(f"your {device.kind} stayed locked; unlock it and open {kept.app} yourself")
        time.sleep(LAUNCH_RETRY)


def app_script(statement: str, bundle_id: str) -> list[str]:
    """An `osascript` command that runs `statement` on the app whose bundle ID is `item 1 of argv`. The ID
    comes from the project's build settings, so it is passed as an argument and never becomes script source."""
    return ["osascript", "-e", "on run argv", "-e", statement, "-e", "end run", bundle_id]


def running(bundle_id: str) -> bool:
    result = subprocess.run(app_script("return application id (item 1 of argv) is running", bundle_id),
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return result.stdout.strip() == "true"


def open_on_mac(state: State, kept: Kept) -> None:
    """Open the kept build on this Mac. Every build of an app shares one bundle ID, so `open` would bring
    forward a copy already running, which therefore quits first. macOS opens an iOS app only from a wrapper:
    a folder named like an app that holds it in `Wrapper/` beside a `WrappedBundle` link to it."""
    bundle_id = kept.record["bundle_id"]
    if running(bundle_id):
        tool(app_script("tell application id (item 1 of argv) to quit", bundle_id))
        deadline = time.monotonic() + QUIT_WAIT
        while running(bundle_id):
            if time.monotonic() >= deadline:
                raise IosError(f"{kept.app} didn't quit; quit it and run "
                               f"`project ios install {kept.record['target']} --mac`")
            time.sleep(1)
    name = kept.record["product"]
    wrapper = state.mac / name
    if wrapper.exists():
        shutil.rmtree(wrapper)
    (wrapper / "Wrapper").mkdir(parents=True)
    copy_app(kept.app_path, wrapper / "Wrapper" / name)
    (wrapper / "WrappedBundle").symlink_to(f"Wrapper/{name}")
    tool(["open", str(wrapper)])
    print(f"Opened {kept.described} on this Mac.")


# Crash reports

def crash_summary(path: Path) -> str:
    """What a crash report says: when, which build, the exception or the termination, and the top of the
    thread that crashed. A watchdog kill crashes no thread, so it shows the main thread, which is the one
    that kept the app from answering."""
    try:
        header_text, _, body_text = path.read_text(errors="replace").partition("\n")
        header, body = json.loads(header_text), json.loads(body_text)
    except ValueError:
        return f"\n{path}\n  not a crash report this command can read"
    lines = ["", str(path),
             f"  {header.get('timestamp')}  {header.get('app_name')} {header.get('app_version')} "
             f"({header.get('build_version')})  {header.get('os_version')}"]
    exception = body.get("exception")
    if exception:
        lines.append(f"  {exception.get('type')} {exception.get('signal', '')} {exception.get('codes', '')}".rstrip())
    termination = body.get("termination") or {}
    if termination:
        code = termination.get("code")
        code = hex(code) if isinstance(code, int) else code
        lines.append(f"  termination {termination.get('namespace', '')} {code}".rstrip())
        lines.extend(f"    {reason}" for reason in termination.get("reasons", []))
    images = body.get("usedImages") or []
    threads = body.get("threads") or []
    crashed = body.get("faultingThread")
    index = 0 if crashed is None else crashed
    if index < len(threads):
        thread = threads[index]
        name = thread.get("queue") or thread.get("name") or ""
        heading = f"  thread {index} {name}".rstrip()
        lines.append(heading if crashed is not None else f"{heading}, the main thread, since no thread crashed")
        for frame in (thread.get("frames") or [])[:FRAMES]:
            image_index = frame.get("imageIndex", -1)
            image = images[image_index] if 0 <= image_index < len(images) else {}
            where = frame.get("symbol") or f"+{frame.get('imageOffset', 0):#x}"
            source = f" {frame['sourceFile']}:{frame.get('sourceLine', '')}" if frame.get("sourceFile") else ""
            lines.append(f"    {image.get('name', '?')}  {where}{source}")
    return "\n".join(lines)


def crashes(state: State, app: Optional[str], device: Optional[Device], output: Path) -> None:
    """Copy the app's newest crash reports from the device, or with no device from this Mac, to `output`,
    keeping a copy already there, and print what each says."""
    product = kept_app(state, app).product
    output.mkdir(parents=True, exist_ok=True)
    pattern = f"{glob.escape(product)}-*.ips"
    if device is None:
        reports = Path.home() / "Library" / "Logs" / "DiagnosticReports"
        found = sorted([*reports.glob(pattern), *(reports / "Retired").glob(pattern)],
                       key=lambda path: path.stat().st_mtime, reverse=True)[:CRASHES]
        if not found:
            print(f"No {product} crash reports on this Mac.")
            return
        for report in found:
            copy = output / report.name
            if not copy.exists():
                shutil.copy2(report, copy)
            print(crash_summary(copy))
        return
    with tempfile.TemporaryDirectory() as folder:
        listing = Path(folder) / "files.json"
        tool(["xcrun", "devicectl", "device", "info", "files", "--quiet", "--device", device.udid,
              "--domain-type", "systemCrashLogs", "--json-output", str(listing)])
        files = json.loads(listing.read_text())["result"]["files"]
    reports = [entry for entry in files
               if not (entry.get("resources") or {}).get("isDirectory")
               and entry["name"].startswith(product + "-") and entry["name"].endswith(".ips")]
    reports.sort(key=lambda entry: (entry.get("metadata") or {}).get("lastModDate", ""), reverse=True)
    if not reports:
        print(f"No {product} crash reports on {device.name}.")
        return
    for entry in reports[:CRASHES]:
        copy = output / Path(entry["relativePath"]).name
        if not copy.exists():
            tool(["xcrun", "devicectl", "device", "copy", "from", "--quiet", "--device", device.udid,
                  "--domain-type", "systemCrashLogs", "--source", entry["relativePath"], "--destination", str(copy)])
        print(crash_summary(copy))


# The log

def log_predicate(bundle_id: str, process: str) -> str:
    """The lines that belong to the app: its subsystem's and the faults its process writes, where a Swift
    `fatalError` message lands. An app logs under a subsystem such as `com.example.myapp` while its
    Development build's bundle ID is `com.example.myapp.dev`, so the bundle ID without its last part
    counts too, unless that leaves a two-part vendor prefix that the vendor's other apps share."""
    subsystems = [f'subsystem == "{bundle_id}"', f'subsystem BEGINSWITH "{bundle_id}."']
    parts = bundle_id.split(".")
    if len(parts) - 1 >= 3:
        subsystems.append(f'subsystem == "{".".join(parts[:-1])}"')
    return f'({" OR ".join(subsystems)}) OR (process == "{process}" AND messageType == fault)'


def local_now() -> datetime:
    return datetime.now()


def claim_log(output: Path, product: str) -> Path:
    """A new file for the log, named like a screenshot. Each name is claimed by an exclusive create, so a
    run in the same second takes the next name rather than writing over this one."""
    stamp = local_now().strftime("%Y-%m-%d at %H.%M.%S")
    for number in range(1, 101):
        path = output / f"{product} log {stamp}{'' if number == 1 else f' {number}'}.txt"
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
        except FileExistsError:
            continue
        return path
    raise IosError(f"can't create a log file in {output}")


def remove_archive(folder: Path) -> None:
    """Delete a device's log archive. `log collect` writes it as root, so plain removal can fail, and sudo,
    whose password the collect already asked for, finishes it."""
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except PermissionError:
        subprocess.run(["sudo", "rm", "-rf", str(folder)], check=False)


def logs(state: State, app: Optional[str], device: Optional[Device], output: Path, span: str,
         predicate: Optional[str]) -> None:
    """Write the app's log lines over the last `span`, from the device or with no device from this Mac, to a
    new file in `output`. A failure or Ctrl-C removes the file and the archive, which holds the whole
    device's log."""
    kept = kept_app(state, app)
    filter_text = predicate or log_predicate(kept.record["bundle_id"], kept.product)
    output.mkdir(parents=True, exist_ok=True)
    path = claim_log(output, kept.product)
    show = ["--style", "compact", "--info", "--debug", "--predicate", filter_text]
    archive: Optional[Path] = None
    try:
        with open(path, "w") as written:
            if device is None:
                subprocess.run(["log", "show", "--last", span, *show], stdout=written, check=True)
            else:
                state.folder.mkdir(parents=True, exist_ok=True)
                archive = Path(tempfile.mkdtemp(prefix="log-", dir=state.folder))
                print(f"Collecting the last {span} of {device.name}'s log. sudo asks for your password, and "
                      "collecting can take a few minutes…", flush=True)
                collected = archive / "device.logarchive"
                subprocess.run(["sudo", "log", "collect", "--device-udid", device.udid, "--last", span,
                                "--output", str(collected)], stdout=subprocess.DEVNULL, check=True)
                subprocess.run(["sudo", "log", "show", str(collected), *show], stdout=written, check=True)
    except BaseException as error:
        path.unlink(missing_ok=True)
        if isinstance(error, subprocess.CalledProcessError):
            raise IosError(f"{' '.join(error.cmd[:3])} exited {error.returncode}, so no log was written") from error
        raise
    finally:
        if archive is not None:
            remove_archive(archive)
    with open(path, errors="replace") as written:
        entries = sum(1 for line in written if LOG_ENTRY.match(line))
    print(f"Wrote {entries} lines of {kept.product}'s log to {path}")


# Cleanup

def folder_size(folder: Path) -> int:
    total = 0
    for parent, _, names in os.walk(folder):
        for name in names:
            try:
                total += (Path(parent) / name).lstat().st_size
            except OSError:
                continue
    return total


def human_size(size: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def cleanup(checkout: Path, state: State) -> None:
    """Remove the worktree, DerivedData, and kept builds. The lock file stays, so a run waiting on it never
    ends up holding a lock on a file this one deleted while another run locks a new one."""
    with locked(state, "cleanup"):
        leftovers = [path for path in state.folder.iterdir() if path.name != state.lock.name]
        if not leftovers:
            review.git("-C", str(checkout), "worktree", "prune")
            print("Nothing to clean up.")
            return
        size = folder_size(state.folder)
        if state.worktree.exists():
            remove_worktree(state.worktree)
        for path in leftovers:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        review.git("-C", str(checkout), "worktree", "prune")
        print(f"Removed {state.folder} ({human_size(size)}): the worktree, DerivedData, and every kept build.")


# Commands

def devices() -> list[Device]:
    require_tools()
    return paired_devices()


def target_device(mac: bool, wanted: Optional[str], kind: Optional[str]) -> Optional[Device]:
    return None if mac else choose_device(paired_devices(), wanted, kind)


def build(checkout: Path, text: str, *, app: Optional[str], project_setting: Optional[str], wanted: Optional[str],
          kind: Optional[str], mac: bool, install: bool, verbose: bool, allow_untrusted: bool) -> None:
    require_tools()
    repo = review.origin_repo(checkout)
    state = state_for(repo)
    device = target_device(mac, wanted, kind) if install else None
    with locked(state, f"build {text}"):
        target = resolve_target(repo, text, allow_untrusted)
        sha = place_worktree(checkout, state, target)
        project = find_project(state.worktree, project_setting)
        name = app or project.stem
        scheme = choose_scheme(project, name)
        destination = MAC_DESTINATION if mac else DEVICE_DESTINATION
        product = read_product(project, scheme, destination, state.derived)
        commit = review.git("-C", str(state.worktree), "log", "-1", "--format=%h %s")
        print(f"Building {scheme}{' for this Mac' if mac else ''} from {target.branch} at {commit}", flush=True)
        run_build(["xcodebuild", "build", *project_args(project), "-scheme", scheme, "-destination", destination,
                   "-derivedDataPath", str(state.derived), "-allowProvisioningUpdates"], state.log, verbose)
        kept = keep(state, name, scheme, target, sha, product, mac)
        if not install:
            return
        if device is None:
            open_on_mac(state, kept)
        else:
            install_on(device, kept)


def install(checkout: Path, text: Optional[str], *, app: Optional[str], wanted: Optional[str], kind: Optional[str],
            mac: bool) -> None:
    require_tools()
    state = state_for(review.origin_repo(checkout))
    device = target_device(mac, wanted, kind)
    with locked(state, f"install {text or ''}".rstrip()):
        kept = kept_target(state, app, text)
        if device is None:
            open_on_mac(state, kept)
        else:
            install_on(device, kept)


def crash_reports(checkout: Path, *, app: Optional[str], wanted: Optional[str], kind: Optional[str], mac: bool,
                  output: Path) -> None:
    require_tools()
    state = state_for(review.origin_repo(checkout))
    crashes(state, app, target_device(mac, wanted, kind), output)


def device_log(checkout: Path, *, app: Optional[str], wanted: Optional[str], kind: Optional[str], mac: bool,
               output: Path, span: str, predicate: Optional[str]) -> None:
    require_tools()
    if not SPAN.fullmatch(span):
        raise UsageError(f"--last takes a span such as 5m, 20m, 2h, or 1d, not {span!r}")
    state = state_for(review.origin_repo(checkout))
    logs(state, app, target_device(mac, wanted, kind), output, span, predicate)


def clean(checkout: Path) -> None:
    require_tools()
    cleanup(checkout, state_for(review.origin_repo(checkout)))
