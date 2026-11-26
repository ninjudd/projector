from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from . import artifact, config, refs, summary, watch
from .inbox import locked, read_json, write_json

PREVIEW_IDLE = 900


def key(root: Path, remote: str, id: str) -> str:
    return hashlib.sha256(json.dumps([str(root.resolve()), remote, id]).encode()).hexdigest()


def catalog(root: Path, remote: str = 'origin') -> list[dict]:
    tips = artifact.ref_versions(root, remote)
    folder = watch.state_root() / 'artifact-catalog' / key(root, remote, '')
    with locked(folder):
        cache = read_json(folder / 'catalog.json') or {}
        if cache.get('tips') == list(tips) and isinstance(cache.get('artifacts'), list):
            return cache['artifacts']
        found = {}
        for tip, preview in zip(tips, (False, True)):
            if not tip:
                continue
            reader = artifact.GitReader(root, tip)
            ids = {path.split('/')[0] for path in reader.entries if path.endswith('/latest.json')}
            for id in sorted(ids):
                try:
                    artifact.identifier(id)
                    pointer = artifact.parse_json(reader.read(f'{id}/latest.json', artifact.MAX_JSON), id)
                    version = pointer.get('version') if isinstance(pointer, dict) else None
                    if not isinstance(version, str) or not artifact.VERSION.fullmatch(version):
                        raise artifact.ArtifactError('invalid latest version')
                    prefix = f'{id}/{version}'
                    meta = artifact.manifest(artifact.parse_json(
                        reader.read(f'{prefix}/artifact.json', artifact.MAX_JSON), prefix))
                    if meta['id'] != id:
                        raise artifact.ArtifactError('artifact ID does not match its directory')
                    found[id] = {'id': id, 'title': meta['title'], 'description': meta['description'],
                                 'version': version, 'preview': preview}
                except (artifact.ArtifactError, refs.RefError) as error:
                    found.pop(id, None)
                    artifact.diagnostic(f'{id}: leaving it out of the panel: {error}')
        rows = sorted(found.values(), key=lambda row: (row['title'].casefold(), row['id']))
        write_json(folder / 'catalog.json', {'tips': list(tips), 'artifacts': rows})
        return rows


def context_folder(session: str, state: Path | None = None) -> Path:
    return watch.session_folder((state or watch.state_root()).parent / 'artifacts', session)


def dismissals(session: str, state: Path | None = None) -> set[str]:
    saved = read_json(context_folder(session, state) / 'artifact-dismissals.json')
    if saved is None:
        legacy = watch.session_folder(state or watch.state_root(), session)
        saved = read_json(legacy / 'artifact-dismissals.json')
    hidden = (saved or {}).get('keys', [])
    return {key for key in hidden if isinstance(key, str)} if isinstance(hidden, list) else set()


def context(session: str, state: Path | None = None) -> dict[str, dict]:
    folder = context_folder(session, state)
    entries = (read_json(folder / 'artifacts.json') or {}).get('artifacts', [])
    found = {}
    if not isinstance(entries, list):
        return found
    for entry in entries:
        if (not isinstance(entry, dict)
                or not all(isinstance(entry.get(name), str) and entry[name] for name in ('checkout', 'remote', 'id'))
                or not Path(entry['checkout']).is_absolute() or not artifact.ID.fullmatch(entry['id'])):
            continue
        found[key(Path(entry['checkout']), entry['remote'], entry['id'])] = {
            name: entry[name] for name in ('checkout', 'remote', 'id')}
    return found


def context_checkout(root: Path, remote: str) -> Path:
    tree = config.working_tree(root)
    if tree is not None:
        main = config.working_tree(tree.common.parent)
        if (main is not None and main.common == tree.common
                and artifact.repository(main.top, remote) == artifact.repository(root, remote)):
            root = main.top
    return root.resolve()


def attach(root: Path, remote: str, session: str, id: str, *, restore: bool = False,
           state: Path | None = None) -> None:
    artifact.identifier(id)
    root = context_checkout(root, remote)
    if not any(row['id'] == id for row in catalog(root, remote)):
        raise artifact.ArtifactError(f'{id}: no saved artifact')
    folder = context_folder(session, state)
    name = key(root, remote, id)
    with locked(folder):
        entries = context(session, state)
        entries[name] = {'checkout': str(root.resolve()), 'remote': remote, 'id': id}
        write_json(folder / 'artifacts.json', {'artifacts': list(entries.values())})
        hidden = dismissals(session, state)
        if restore:
            hidden.discard(name)
        write_json(folder / 'artifact-dismissals.json', {'keys': sorted(hidden)})


def listing(session: str, state: Path | None = None) -> list[dict]:
    hidden = dismissals(session, state)
    catalogs = {}
    found = []
    for name, entry in context(session, state).items():
        if name in hidden:
            continue
        root, remote = Path(entry['checkout']), entry['remote']
        location = (root, remote)
        if location not in catalogs:
            try:
                catalogs[location] = {row['id']: row for row in catalog(root, remote)}
            except (artifact.ArtifactError, refs.RefError, OSError) as error:
                artifact.diagnostic(f'{root}: could not read session artifacts: {error}')
                catalogs[location] = {}
        row = catalogs[location].get(entry['id'])
        if row is not None:
            found.append({**row, **entry, 'key': name})
    return sorted(found, key=lambda row: (row['title'].casefold(), row['id'], row['key']))


def discussion(row: dict) -> str:
    lines = [f"Artifact {row['id']}@{row['version']}: {row['title']}"]
    if row['description']:
        lines.append(f"Description: {row['description']}")
    lines += [f"Checkout: {row['checkout']}", f"Remote: {row['remote']}"]
    return '; '.join(' '.join(line.splitlines()) for line in lines) + '. '


def dismiss(root: Path, remote: str, session: str, id: str, *, state: Path | None = None) -> None:
    artifact.identifier(id)
    root = context_checkout(root, remote)
    folder = context_folder(session, state)
    name = key(root, remote, id)
    with locked(folder):
        path = folder / 'artifact-dismissals.json'
        hidden = dismissals(session, state)
        hidden.add(name)
        write_json(path, {'keys': sorted(hidden)})


def open_url(root: Path, remote: str, id: str) -> str:
    artifact.identifier(id)
    row = next((row for row in catalog(root, remote) if row['id'] == id), None)
    if row is None:
        raise artifact.ArtifactError(f'{id}: no saved artifact')
    if not row['preview']:
        repo = artifact.repository(root, remote)
        url, _ = summary.push_decision(repo) if repo else (None, '')
        if url:
            return f'{url}artifacts/{id}/'
    tip = artifact.ref_versions(root, remote)[1 if row['preview'] else 0]
    saved = artifact.read_version(artifact.GitReader(root, tip), f"{id}/{row['version']}")
    folder = watch.state_root() / 'artifact-previews' / key(root, remote, id)
    with locked(folder):
        path = folder / 'preview.json'
        previous = read_json(path) or {}
        url = previous.get('url', '')
        if previous.get('version') == saved.version and live_preview(url):
            return url
        work = Path(tempfile.mkdtemp(prefix='projector-artifact-open-'))
        process = None
        try:
            artifact.write_source(saved, work / 'files')
            ready = work / 'ready.json'
            token = secrets.token_hex(16)
            detached = ({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                        if os.name == 'nt' else {'start_new_session': True})
            process = subprocess.Popen(
                [sys.executable, '-m', 'projector.artifact_panel', str(work), token],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parent.parent)}, **detached)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                record = read_json(ready)
                if record and live_preview(record.get('url', '')):
                    write_json(path, {**record, 'version': saved.version})
                    threading.Thread(target=process.wait, daemon=True).start()
                    return record['url']
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            raise artifact.ArtifactError('the local artifact preview did not start')
        except BaseException:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            shutil.rmtree(work, ignore_errors=True)
            raise


def live_preview(url: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.path.startswith('/'):
            return False
        with urllib.request.urlopen(url, timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def serve(work: Path, token: str, idle: float = PREVIEW_IDLE) -> None:
    prefix = f'/{token}/'

    class Server(http.server.HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address

    class Handler(http.server.SimpleHTTPRequestHandler):
        def route(self):
            host = self.headers.get('Host', '').split(':')[0]
            if host != '127.0.0.1' or not self.path.startswith(prefix):
                self.send_error(404)
                return False
            server.last_request = time.monotonic()
            return True

        def translate_path(self, path):
            return super().translate_path(path[len(prefix) - 1:])

        def do_GET(self):
            if self.route():
                super().do_GET()

        def do_HEAD(self):
            if self.route():
                super().do_HEAD()

        def log_message(self, format, *args):
            pass

    try:
        with Server(('127.0.0.1', 0), partial(Handler, directory=str(work / 'files'))) as server:
            server.timeout = 1
            server.last_request = time.monotonic()
            url = f'http://127.0.0.1:{server.server_port}/{token}/index.html'
            write_json(work / 'ready.json', {'url': url, 'pid': os.getpid()})
            while time.monotonic() - server.last_request < idle:
                server.handle_request()
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    serve(Path(sys.argv[1]), sys.argv[2])
