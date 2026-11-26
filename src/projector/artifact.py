from __future__ import annotations

import datetime
import hashlib
import html
import json
import re
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable

from . import refs, summary
from .core import ProjectorError

REF = 'refs/projector/artifacts'
FOLDER = 'artifacts'
MAX_FILE = 25 * 1024 * 1024
MAX_BUNDLE = 50 * 1024 * 1024
MAX_FILES = 1000
MAX_JSON = 1024 * 1024
ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
VERSION = re.compile(r'[0-9a-f]{64}\Z')


class ArtifactError(ProjectorError):
    pass


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value) or len(value) > 80:
        raise ArtifactError(f'{value!r}: use a lowercase artifact ID of at most 80 letters, digits, and hyphens')
    return value


def file_path(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or PureWindowsPath(value).drive
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(not part or part.startswith('.') for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise ArtifactError(f'{value!r}: expected a regular relative path without hidden components or traversal')
    return value


def manifest(value: dict) -> dict:
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ArtifactError('artifact.json needs schema 1')
    if set(value) - {'schema', 'id', 'title', 'description', 'entry', 'files'}:
        raise ArtifactError('artifact.json has unknown fields')
    identifier(value.get('id'))
    for name, limit in [('title', 120), ('description', 500)]:
        text = value.get(name, '')
        if not isinstance(text, str) or len(text) > limit or (name == 'title' and not text.strip()):
            raise ArtifactError(f'artifact.json needs {name} as plain text of at most {limit} characters')
    paths = value.get('files')
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
        raise ArtifactError(f'artifact.json needs between 1 and {MAX_FILES} files')
    seen = {}
    for path in paths:
        file_path(path)
        if path == 'artifact.json':
            raise ArtifactError('artifact.json is metadata; omit it from files')
        if path.casefold() in seen:
            raise ArtifactError(f'{path}: duplicate path or case collision')
        seen[path.casefold()] = path
    for path in paths:
        for parent in PurePosixPath(path).parents:
            if str(parent).casefold() in seen:
                raise ArtifactError(f'{path}: a parent is listed as a file')
    if value.get('entry') != 'index.html' or 'index.html' not in paths:
        raise ArtifactError('artifact.json needs entry index.html and must list index.html in files')
    return {**value, 'description': value.get('description', ''), 'files': sorted(paths)}


def json_bytes(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    except (UnicodeError, TypeError, ValueError) as error:
        raise ArtifactError('artifact metadata must contain UTF-8 JSON values') from error


def parse_json(data: bytes, name: str):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise ArtifactError(f'{name}: invalid JSON: {error}') from error


def safe_read(root: Path, path: str, limit: int) -> bytes:
    file_path(path)
    current = root
    if root.is_symlink():
        raise ArtifactError(f'{root}: symlinks are not supported')
    try:
        for part in path.split('/'):
            current = current / part
            if current.is_symlink():
                raise ArtifactError(f'{path}: symlinks are not supported')
        info = current.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ArtifactError(f'{path}: expected a regular file of at most {limit} bytes')
        with current.open('rb') as source:
            data = source.read(limit + 1)
        if len(data) > limit:
            raise ArtifactError(f'{path}: exceeds {limit} bytes')
        return data
    except OSError as error:
        raise ArtifactError(f'{path}: {error.strerror}') from error


@dataclass
class Bundle:
    manifest: dict
    files: dict[str, bytes]
    version: str
    records: list[dict]
    published_at: str = ''

    @property
    def id(self) -> str:
        return self.manifest['id']

    def revision(self) -> dict:
        return {'version': self.version, 'publishedAt': self.published_at, 'files': self.records}


def bundle(meta: dict, read: Callable[[str, int], bytes]) -> Bundle:
    meta = manifest(meta)
    files, records, total = {}, [], 0
    for path in meta['files']:
        data = read(path, min(MAX_FILE, MAX_BUNDLE - total))
        total += len(data)
        files[path] = data
        records.append({'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    if len(json_bytes(meta)) > MAX_JSON or len(json_bytes(records)) > MAX_JSON - 256:
        raise ArtifactError(f'artifact metadata exceeds {MAX_JSON} bytes')
    version = hashlib.sha256(json_bytes({'manifest': meta, 'files': records})).hexdigest()
    return Bundle(meta, files, version, records)


def snapshot(directory: Path) -> Bundle:
    value = parse_json(safe_read(directory, 'artifact.json', MAX_JSON), 'artifact.json')
    return bundle(value, partial(safe_read, directory))


def write_source(saved: Bundle, directory: Path) -> None:
    if directory.exists() or directory.is_symlink():
        raise ArtifactError(f'{directory}: already exists; choose a new working directory')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent, prefix='.artifact-') as temp:
        work = Path(temp) / 'bundle'
        work.mkdir()
        (work / 'artifact.json').write_bytes(json_bytes(saved.manifest) + b'\n')
        for path, data in saved.files.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        work.rename(directory)


def init(id: str, directory: Path, title: str, description: str = '') -> None:
    meta = manifest({'schema': 1, 'id': id, 'title': title, 'description': description,
                     'entry': 'index.html', 'files': ['index.html']})
    page = ('<!doctype html>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{html.escape(title)}</title>\n<h1>{html.escape(title)}</h1>\n')
    write_source(bundle(meta, lambda path, limit: page.encode('utf-8')), directory)


class GitReader:
    def __init__(self, root: Path, ref: str, prefix: str = FOLDER):
        self.root = root
        self.entries = {}
        self.blobs = {}
        listing = refs.git_bytes('ls-tree', '-r', '-l', '-z', ref, '--', prefix, root=root)
        for record in listing.split(b'\0'):
            if not record:
                continue
            header, path = record.split(b'\t', 1)
            mode, kind, oid, size = header.split()
            try:
                name = path.decode('utf-8').removeprefix(FOLDER + '/')
            except UnicodeError:
                continue
            self.entries[name] = (mode, kind, oid.decode(), int(size) if size.isdigit() else -1)

    def read(self, path: str, limit: int) -> bytes:
        record = self.entries.get(path)
        if record is None:
            raise ArtifactError(f'{path}: missing from the saved version')
        mode, kind, oid, size = record
        if mode not in (b'100644', b'100755') or kind != b'blob' or not 0 <= size <= limit:
            raise ArtifactError(f'{path}: expected a regular blob of at most {limit} bytes')
        if oid not in self.blobs:
            self.blobs[oid] = refs.git_bytes('cat-file', 'blob', oid, root=self.root)
        return self.blobs[oid]

    def manifests(self) -> list[str]:
        return [name for name in self.entries if name.count('/') == 2 and name.endswith('/artifact.json')]


class DirectoryReader:
    def __init__(self, root: Path):
        self.root = root

    def read(self, path: str, limit: int) -> bytes:
        return safe_read(self.root, path, limit)

    def manifests(self) -> list[str]:
        paths = []
        if not self.root.exists():
            return paths
        if self.root.is_symlink():
            raise ArtifactError(f'{self.root}: symlinks are not supported')
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            for version in sorted(folder.iterdir()):
                if version.is_dir() and not version.is_symlink():
                    paths.append(f'{folder.name}/{version.name}/artifact.json')
        return paths


def read_version(reader, prefix: str) -> Bundle:
    id, version = prefix.split('/')
    identifier(id)
    if not VERSION.fullmatch(version):
        raise ArtifactError(f'{prefix}: invalid version ID')
    meta = parse_json(reader.read(f'{prefix}/artifact.json', MAX_JSON), f'{prefix}/artifact.json')
    saved = bundle(meta, lambda path, limit: reader.read(f'{prefix}/files/{path}', limit))
    record = parse_json(reader.read(f'{prefix}/revision.json', MAX_JSON), f'{prefix}/revision.json')
    if not isinstance(record, dict) or (saved.id, saved.version) != (id, version) or \
            record.get('version') != version or record.get('files') != saved.records:
        raise ArtifactError(f'{prefix}: version or file hashes do not match the saved bytes')
    try:
        published = record['publishedAt']
        when = datetime.datetime.fromisoformat(published.replace('Z', '+00:00'))
        if when.utcoffset() != datetime.timedelta(0):
            raise ValueError('expected UTC')
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ArtifactError(f'{prefix}: invalid UTC publication time') from error
    saved.published_at = published
    return saved


@dataclass
class Saved:
    versions: dict[str, Bundle] = field(default_factory=dict)
    latest: str | None = None
    preview: bool = False


def diagnostic(message: str) -> None:
    print(f'artifact: {message}', file=sys.stderr)


def read_saved(reader, report: Callable[[str], None] = diagnostic) -> dict[str, Saved]:
    found: dict[str, Saved] = {}
    for path in reader.manifests():
        prefix = path.rsplit('/', 1)[0]
        try:
            item = read_version(reader, prefix)
        except (ArtifactError, refs.RefError) as error:
            report(f'skipping {prefix}: {error}')
            continue
        found.setdefault(item.id, Saved()).versions[item.version] = item
    for id, saved in found.items():
        try:
            pointer = parse_json(reader.read(f'{id}/latest.json', MAX_JSON), f'{id}/latest.json')
            version = pointer.get('version') if isinstance(pointer, dict) else None
            if not isinstance(version, str) or version not in saved.versions:
                raise ArtifactError('latest does not name a valid saved version')
            saved.latest = version
        except ArtifactError as error:
            report(f'{id}: {error}; leaving it out of the directory')
    return found


def read_refs(root: Path, remote: str, report: Callable = diagnostic) -> dict[str, Saved]:
    found: dict[str, Saved] = {}
    for name in (refs.tracking_ref(remote, REF), REF):
        if not refs.ref_tip(root, name):
            continue
        loaded = read_saved(GitReader(root, name), report)
        for id, saved in loaded.items():
            target = found.setdefault(id, Saved())
            for version, item in saved.versions.items():
                target.versions.setdefault(version, item)
            target.latest = saved.latest
            target.preview = name == REF
    return found


def recover(saved: Saved, directory: Path, version: str | None = None) -> None:
    selected = version or saved.latest
    if selected not in saved.versions:
        raise ArtifactError(f'{selected!r}: no saved artifact version; use the full version ID')
    write_source(saved.versions[selected], directory)


def publish(source: Bundle, root: Path, remote: str, *, push: bool) -> Bundle:
    run = lambda *args, **kwargs: refs.git(*args, root=root, **kwargs)
    source.published_at = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    result = [source]
    prefix = f'{source.id}/{source.version}'

    def tree(parent: str) -> str:
        if parent:
            reader = GitReader(root, parent, f'{FOLDER}/{prefix}')
            if f'{prefix}/artifact.json' in reader.entries:
                result[0] = read_version(reader, prefix)
                return run('rev-parse', f'{parent}^{{tree}}')
        files = {f'{FOLDER}/{prefix}/files/{path}': data for path, data in source.files.items()}
        files[f'{FOLDER}/{prefix}/artifact.json'] = json_bytes(source.manifest) + b'\n'
        files[f'{FOLDER}/{prefix}/revision.json'] = json_bytes(source.revision()) + b'\n'
        files[f'{FOLDER}/{source.id}/latest.json'] = json_bytes({'version': source.version}) + b'\n'
        return refs.write_tree(parent, files, run=run)

    update = refs.publish(REF, remote, tree, f'Publish artifact {source.id} at {source.version[:12]}',
                          push=push, run=run)
    verb = 'pushed' if push else 'saved locally'
    print(f'{verb} {source.id}/{source.version}' if update.commit else
          f'already saved {source.id}/{source.version}; latest is unchanged')
    return result[0]


def public(id: str, saved: Saved) -> dict:
    versions = [{'version': item.version, 'title': item.manifest['title'],
                 'description': item.manifest['description'], 'publishedAt': item.published_at,
                 'entry': f'artifacts/{id}/{item.version}/index.html'} for item in saved.versions.values()]
    versions.sort(key=lambda item: (item['publishedAt'], item['version']), reverse=True)
    return {'id': id, 'latest': saved.latest, 'versions': versions, 'preview': saved.preview}


def fetch(root: Path, remote: str, prompt: bool = True) -> str:
    try:
        refs.fetch_ref(remote, REF, run=partial(refs.git, root=root), prompt=prompt)
    except refs.RefError as error:
        return str(error)
    return ''


def ref_versions(root: Path, remote: str) -> tuple:
    return tuple(refs.ref_tip(root, name) for name in (refs.tracking_ref(remote, REF), REF))


def repository(root: Path, remote: str) -> str:
    try:
        url = refs.git('remote', 'get-url', remote, root=root)
    except refs.RefError:
        return ''
    if not url.startswith(('git@github.com:', 'ssh://git@github.com/', 'https://github.com/')):
        return ''
    name = summary.repo_slug(url)
    return name if re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name) else ''


def run(arguments) -> int:
    root = summary.checkout_root(arguments.checkout)
    if root is None:
        raise ArtifactError('artifact commands need a Git checkout; pass --checkout')
    command, remote = arguments.artifact_command, arguments.remote
    if command == 'init' and not arguments.from_published:
        if arguments.version or not arguments.title:
            raise ArtifactError('a new artifact needs --title; --version goes with --from-published')
        init(arguments.id, arguments.dir, arguments.title, arguments.description)
        print(f'wrote {arguments.dir}/artifact.json and index.html')
        return 0
    if command in ('list', 'init'):
        if not arguments.no_fetch:
            reason = fetch(root, remote)
            if reason:
                diagnostic(f'using cached artifacts: {reason}')
        saved = read_refs(root, remote)
        id = arguments.id
        if id:
            identifier(id)
            if id not in saved:
                raise ArtifactError(f'{id}: no saved artifact')
        if command == 'init':
            if arguments.title or arguments.description:
                raise ArtifactError('--from-published keeps the saved metadata; edit it after recovery')
            recover(saved[id], arguments.dir, arguments.version)
            print(f'recovered {id} to {arguments.dir}')
        else:
            rows = [public(name, value) for name, value in sorted(saved.items()) if id is None or name == id]
            if arguments.json_output:
                print(json.dumps({'artifacts': rows}, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    if id:
                        for version in row['versions']:
                            marker = ' latest' if version['version'] == row['latest'] else ''
                            if marker and row['preview']:
                                marker += ' (local preview)'
                            print(f"{version['version']} {version['publishedAt']}{marker}")
                    else:
                        newest = next((v for v in row['versions'] if v['version'] == row['latest']), None)
                        print(f"{row['id']}\t{newest['title'] if newest else '(no latest)'}\t"
                              f"{row['latest']}\t{len(row['versions'])} versions"
                              f"{' (local preview)' if row['preview'] else ''}")
        return 0
    source = snapshot(arguments.dir)
    url, reason = None, ''
    repo = ''
    if not arguments.local:
        repo = repository(root, remote)
        if repo:
            url, reason = summary.push_decision(repo)
        else:
            reason = f'{remote} is not a GitHub repository'
    saved = publish(source, root, remote, push=url is not None)
    if url is None:
        if reason:
            print(f'nothing pushed: {reason}')
        print(f'preview with project site serve at artifacts/{saved.id}/')
        return 0
    print(f'latest: {url}artifacts/{saved.id}/')
    print(f'version: {url}artifacts/{saved.id}/{saved.version}/')
    if not arguments.no_dispatch:
        workflow = Path(summary.WORKFLOW_PATH).name
        branch = summary.default_branch(remote, root)
        try:
            path = summary.site_workflow(repo)
            if path is None:
                raise summary.SummaryError('the site workflow is missing')
            workflow = Path(path).name
            branch = summary.gh('api', f'repos/{repo}', '--jq', '.default_branch').strip()
            if not branch:
                raise summary.SummaryError('could not read the remote default branch')
            summary.gh('api', f'repos/{repo}/actions/workflows/{workflow}/dispatches', '-f', f'ref={branch}')
        except summary.SummaryError as error:
            raise ArtifactError(f'content is saved, but deployment did not start: {error}; retry with '
                                f'gh workflow run {workflow} --repo {repo} --ref {branch}') from error
        print('deployment requested; the site updates after its workflow succeeds')
    return 0
