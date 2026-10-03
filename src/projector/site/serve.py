"""Serve the Projector site from a checkout, without GitHub Pages.

`project site serve` builds the same static site a deploy does into a
temporary directory and serves it over HTTP. It reads summaries from the
checkout's copy of the hidden summaries ref, so it needs no GitHub Pages
site and no workflow. While it runs it watches the files the site is built
from and the summaries ref, fetching the ref again every minute, and
rebuilds when either changes, as each publish redeploys a Pages site.
"""

from __future__ import annotations

import http.server
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from ..summary import LEGACY_PAGES_REF, LEGACY_PAGES_ROOT, PAGES_REF, PAGES_ROOT

ThreadingHTTPServer = http.server.ThreadingHTTPServer
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
WILDCARD = {"", "0.0.0.0", "::"}
# How often a running server fetches the summaries ref, in seconds.
FETCH_INTERVAL = 60.0


def run_git(root: Path, *args: str, detached: bool = False) -> subprocess.CompletedProcess:
    """Run git in `root`. `detached` runs it in a session of its own with no input and git's prompts off,
    so neither git nor an ssh it starts can ask for credentials on the server's terminal."""
    if not detached:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, stdin=subprocess.DEVNULL,
                          start_new_session=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def tracking_ref(remote: str, ref: str = PAGES_REF) -> str:
    """Where `summary publish` keeps its copy of the remote's summaries ref."""
    return ref.replace("refs/projector/", f"refs/projector/remotes/{remote}/", 1)


def fetch_summaries(root: Path, remote: str, ref: str = PAGES_REF, prompt: bool = True) -> str:
    """Update the local copy of the remote's summaries ref, and say why when it cannot.

    A repository that has not published since walkthroughs were renamed
    summaries has only the old walkthroughs ref, so that ref is fetched in the
    new one's place, and `summaries_refs` reads it until the new ref exists.
    A server fetches every minute, so the fetch writes only the tracking ref
    and never FETCH_HEAD, which the user's own fetch may have just written.
    Without `prompt`, the fetch runs detached from the terminal, so a remote
    that wants a password or an ssh key's passphrase fails the fetch rather
    than asking for it from a thread no one is watching.
    """
    fetch = ("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", remote)
    local = tracking_ref(remote, ref)
    fetched = run_git(root, *fetch, f"+{ref}:{local}", detached=not prompt)
    if fetched.returncode == 0:
        return ""
    if ref == PAGES_REF:
        legacy = run_git(root, *fetch, f"+{LEGACY_PAGES_REF}:{tracking_ref(remote, LEGACY_PAGES_REF)}",
                         detached=not prompt)
        if legacy.returncode == 0:
            return ""
    # Git's first line names what failed; for an unreachable remote the last
    # is only "and the repository exists."
    reason = fetched.stderr.decode(errors="replace").strip().splitlines()
    return reason[0] if reason else f"git fetch exited {fetched.returncode}"


def summaries_refs(root: Path, remote: str, ref: str = PAGES_REF, folder: str = PAGES_ROOT) -> list[tuple[str, str]]:
    """The local refs holding summaries, each with the folder in it that holds the summaries.

    The remote's copy comes first and this checkout's own ref after it, which
    `summary publish` writes when it keeps a summary local, so the site shows
    both and a summary in both is read from the local one. The old walkthroughs
    ref, with its summaries under walkthroughs/, is read only when no summaries
    ref exists, as in a repository that has not published since the rename,
    and then only one copy of it, the remote's when there is one.
    """
    def exists(name: str) -> bool:
        return run_git(root, "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}").returncode == 0

    found = [(name, folder) for name in (tracking_ref(remote, ref), ref) if exists(name)]
    if found or ref != PAGES_REF:
        return found
    return [(name, LEGACY_PAGES_ROOT) for name in (tracking_ref(remote, LEGACY_PAGES_REF), LEGACY_PAGES_REF)
            if exists(name)][:1]


def ref_commit(root: Path, ref: str | None) -> str:
    if ref is None:
        return ""
    found = run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return found.stdout.decode().strip() if found.returncode == 0 else ""


def extract_summaries(root: Path, ref: str, dest: Path, folder: str = PAGES_ROOT) -> Path | None:
    """Write the summaries on `ref` under `dest`, each dated by the commit that last changed it.

    The build orders a pull request's summaries by when each summary was
    committed. An archive stamps every file with the ref's newest commit, so
    each summary's time is set from its own history instead.
    """
    archive = run_git(root, "archive", "--format=tar", ref, folder)
    if archive.returncode != 0:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:
            tar.extractall(dest)
    log = run_git(root, "log", "--format=%x01%ct", "--name-only", ref, "--", folder).stdout.decode()
    dated: set[str] = set()
    when = 0
    for line in log.splitlines():
        if line.startswith("\x01"):
            when = int(line[1:])
        elif line and line not in dated:
            dated.add(line)
            path = dest / line
            if path.is_file():
                os.utime(path, (when, when))
    return dest / folder


class Summaries:
    """The summaries published to a checkout's remote, looked up afresh on every look.

    A server outlives the ref it started with: `summary publish` may create
    the ref after the server starts, and each fetch moves it, so every look
    finds the ref again rather than keeping the one found at startup.
    """

    def __init__(self, root: Path, remote: str) -> None:
        self.root, self.remote = root, remote

    def fetch(self, prompt: bool = True) -> str:
        """Update the local copy of the remote's summaries, and say why when it cannot."""
        return fetch_summaries(self.root, self.remote, prompt=prompt)

    def fetch_quietly(self) -> str:
        """Fetch as a background thread must, failing rather than prompting for credentials."""
        return self.fetch(prompt=False)

    def version(self) -> tuple:
        """The refs the summaries are read from and their commits, which move when a summary is published."""
        return tuple((name, ref_commit(self.root, name)) for name, _ in summaries_refs(self.root, self.remote))

    def extract(self, dest: Path) -> Path | None:
        """Write the published summaries under `dest`, or return None when nothing is published.

        Each ref is written over the one before, so this checkout's own ref
        wins where it holds the same summary as the remote's copy.
        """
        out = None
        for name, folder in summaries_refs(self.root, self.remote):
            out = extract_summaries(self.root, name, dest, folder) or out
        return out


def keep_fetching(fetch: Callable[[], str], stop: threading.Event, interval: float,
                  report: Callable[[str], None], last: str = "") -> None:
    """Fetch the summaries every `interval` seconds until `stop`, reporting a failure once.

    A failure is reported again only when its reason changes, so a remote with
    nothing published yet does not repeat itself every minute. `last` is the
    failure already reported, if any.
    """
    while not stop.wait(interval):
        reason = fetch()
        if reason and reason != last:
            report(f"could not fetch summaries: {reason}")
        last = reason


def fingerprint(paths: list[Path]) -> tuple:
    """Every file under `paths` with its size and modification time."""
    seen = []
    for top in paths:
        if top.is_file():
            stat = top.stat()
            seen.append((str(top), stat.st_mtime_ns, stat.st_size))
            continue
        for folder, names, files in os.walk(top):
            names[:] = sorted(name for name in names if name != ".git")
            for name in sorted(files):
                path = Path(folder) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                seen.append((str(path), stat.st_mtime_ns, stat.st_size))
    return tuple(seen)


class Site:
    """The newest build of the site, rebuilt in a fresh directory whenever its sources change.

    A request in flight keeps reading the directory it started in, so the
    build it replaced is removed one rebuild later rather than at once.
    """

    def __init__(self, build: Callable[[Path], str], sources: Callable[[], tuple],
                 prepare: Callable[[], None] | None = None) -> None:
        self.build, self.sources, self.prepare = build, sources, prepare
        self.scratch = Path(tempfile.mkdtemp(prefix="projector-site-"))
        self.lock = threading.Lock()
        self.count = 0
        self.current: Path | None = None
        self.previous: Path | None = None
        self.seen: tuple | None = None

    def refresh(self, force: bool = False) -> str | None:
        """Rebuild when the sources changed since the last build; return the build's summary."""
        with self.lock:
            seen = self.sources()
            if not force and seen == self.seen:
                return None
            # A build that fails is retried on the next change, not every poll.
            self.seen = seen
            if self.prepare is not None:
                # What the command generates is not a change of its own, or every
                # rebuild would start the next; only an edit after it counts.
                try:
                    self.prepare()
                finally:
                    self.seen = self.sources()
            self.count += 1
            out = self.scratch / str(self.count)
            try:
                summary = self.build(out)
            except BaseException:
                shutil.rmtree(out, ignore_errors=True)
                raise
            stale, self.previous, self.current = self.previous, self.current, out
        if stale is not None:
            shutil.rmtree(stale, ignore_errors=True)
        return summary

    def close(self) -> None:
        shutil.rmtree(self.scratch, ignore_errors=True)


def host_name(header: str) -> str:
    """The name in a Host header, without its port or an IPv6 address's brackets."""
    header = header.strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def allowed_hosts(host: str) -> set[str] | None:
    """The Host names the server answers to, or None for any when it listens on every address.

    Answering any Host would let DNS rebinding read a loopback server: a page
    in the reader's browser points its own name at 127.0.0.1 and fetches the
    site as a same-origin request. A wildcard address is an explicit choice
    to be reached by names the server cannot know, and it is warned about.
    """
    if host in WILDCARD:
        return None
    return LOOPBACK | {host_name(host)}


def handler(site: Site, base: str, hosts: set[str] | None = LOOPBACK) -> type[http.server.SimpleHTTPRequestHandler]:
    """A request handler that serves the site's newest build under `base`.

    A path the build has no file for gets the not-found page with status 404,
    as GitHub Pages answers it. A request naming a host outside `hosts` gets
    403 and no content.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=str(site.current), **kwargs)

        def send_head(self):
            if hosts is not None and host_name(self.headers.get("Host", "")) not in hosts:
                self.send_error(403, "this server answers only to the names it listens on")
                return None
            if not (urlsplit(self.path).path + "/").startswith(base):
                return self.not_found()
            local = Path(self.translate_path(self.path))
            if not local.exists() or (local.is_dir() and not (local / "index.html").is_file()):
                return self.not_found()
            return super().send_head()

        def translate_path(self, path: str) -> str:
            # The site's files sit at the build's root; the base is only in the URL.
            return super().translate_path(urlsplit(path).path[len(base) - 1:] or "/")

        def not_found(self):
            page = Path(self.directory) / "404.html"
            body = page.read_bytes() if page.is_file() else b"Not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

        def end_headers(self) -> None:
            # A rebuilt page must never be served from the browser's cache.
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


def watch(site: Site, stop: threading.Event, interval: float, report: Callable[[str], None]) -> None:
    while not stop.wait(interval):
        try:
            summary = site.refresh()
        except Exception as error:  # A broken edit must not stop the server.
            report(f"rebuild failed: {error}")
            continue
        if summary:
            report(f"rebuilt: {summary}")
