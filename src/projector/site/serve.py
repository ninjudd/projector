"""Serve the Projector site from a checkout, without GitHub Pages.

`project site serve` builds the same static site a deploy does into a
temporary directory and serves it over HTTP. It reads walkthroughs from the
checkout's copy of the hidden walkthroughs ref, so it needs no GitHub Pages
site, no workflow, and no network once the ref is fetched. While it runs it
watches the files the site is built from and rebuilds when they change.
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

from ..walkthrough import PAGES_REF, PAGES_ROOT

ThreadingHTTPServer = http.server.ThreadingHTTPServer
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def tracking_ref(remote: str, ref: str = PAGES_REF) -> str:
    """Where `walkthrough publish` keeps its copy of the remote's walkthroughs ref."""
    return ref.replace("refs/projector/", f"refs/projector/remotes/{remote}/", 1)


def fetch_walkthroughs(root: Path, remote: str, ref: str = PAGES_REF) -> str:
    """Update the local copy of the remote's walkthroughs ref, and say why when it cannot."""
    local = tracking_ref(remote, ref)
    fetched = run_git(root, "fetch", "--quiet", "--no-tags", remote, f"+{ref}:{local}")
    if fetched.returncode == 0:
        return ""
    reason = fetched.stderr.decode(errors="replace").strip().splitlines()
    return reason[-1] if reason else f"git fetch exited {fetched.returncode}"


def walkthroughs_ref(root: Path, remote: str, ref: str = PAGES_REF) -> str | None:
    """The local ref holding published walkthroughs, preferring the remote's copy."""
    for candidate in (tracking_ref(remote, ref), ref):
        if run_git(root, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}").returncode == 0:
            return candidate
    return None


def ref_commit(root: Path, ref: str | None) -> str:
    if ref is None:
        return ""
    found = run_git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return found.stdout.decode().strip() if found.returncode == 0 else ""


def extract_walkthroughs(root: Path, ref: str, dest: Path, folder: str = PAGES_ROOT) -> Path | None:
    """Write the specs on `ref` under `dest`, each dated by the commit that last changed it.

    The build orders a pull request's walkthroughs by when each spec was
    committed. An archive stamps every file with the ref's newest commit, so
    each spec's time is set from its own history instead.
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

    def __init__(self, build: Callable[[Path], str], sources: Callable[[], tuple]) -> None:
        self.build, self.sources = build, sources
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


def handler(site: Site, base: str) -> type[http.server.SimpleHTTPRequestHandler]:
    """A request handler that serves the site's newest build under `base`.

    A path the build has no file for gets the not-found page with status 404,
    as GitHub Pages answers it.
    """

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, directory=str(site.current), **kwargs)

        def send_head(self):
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
