from __future__ import annotations

import os
import random
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple

from .core import ProjectorError

ATTEMPTS = 5
RACE_SIGNS = ('non-fast-forward', 'fetch first', 'stale info', 'cannot lock ref', 'failed to update ref')


class RefError(ProjectorError):
    pass


def git_bytes(*args: str, root: Path | None = None, env: dict | None = None,
              input: str | bytes | None = None, prompt: bool = True) -> bytes:
    command = ['git', *(['-C', str(root)] if root else []), *args]
    data = input.encode('utf-8') if isinstance(input, str) else input
    result = subprocess.run(command, capture_output=True, input=data,
                            stdin=subprocess.DEVNULL if not prompt and data is None else None,
                            env=env if prompt else
                            {**(env or os.environ), 'GIT_TERMINAL_PROMPT': '0'}, start_new_session=not prompt)
    if result.returncode:
        raise RefError(f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout


def git(*args: str, **kwargs) -> str:
    return git_bytes(*args, **kwargs).decode('utf-8', errors='replace').strip()


def tracking_ref(remote: str, ref: str) -> str:
    return ref.replace('refs/projector/', f'refs/projector/remotes/{remote}/', 1)


def ref_tip(root: Path, ref: str) -> str:
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'],
                            capture_output=True)
    return result.stdout.decode().strip() if result.returncode == 0 else ''


def fetch_ref(remote: str, ref: str, run: Callable = git, strict: bool = True, prompt: bool = True) -> str:
    local = tracking_ref(remote, ref)
    try:
        run('fetch', '--quiet', '--no-tags', '--no-write-fetch-head', remote, f'+{ref}:{local}', prompt=prompt)
    except RefError as error:
        if strict and not any(sign in str(error) for sign in ("couldn't find remote ref", 'remote ref does not exist')):
            raise
        return ''
    return run('rev-parse', '--verify', '--quiet', local)


def write_tree(parent: str, files: dict[str, str | bytes], run: Callable = git,
               blobs: dict[str, str] | None = None) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, 'GIT_INDEX_FILE': str(Path(tmp) / 'index')}
        if parent:
            run('read-tree', parent, env=env)
        entries = dict(blobs or {})
        for name, content in files.items():
            entries[name] = run('hash-object', '-w', '--stdin', input=content)
        run('update-index', '-z', '--index-info', env=env,
            input=''.join(f'100644 {blob}\t{name}\0' for name, blob in sorted(entries.items())))
        return run('write-tree', env=env)


class Update(NamedTuple):
    commit: str
    parent: str
    attempts: int


def publish(ref: str, remote: str, tree: Callable[[str], str], message: str, *, push: bool,
            run: Callable = git, seed: Callable[[], str] | None = None,
            fetch: Callable[[], str] | None = None, sleep: Callable | None = None) -> Update:
    sleep = sleep or time.sleep
    fetch = fetch or (lambda: fetch_ref(remote, ref, run=run))
    tip = fetch() if push else _tip(ref, run)
    initial = (seed() if seed else '') if push and not tip else ''
    for attempt in range(ATTEMPTS):
        parent = tip or initial
        written = tree(parent)
        if parent and written == run('rev-parse', f'{parent}^{{tree}}'):
            return Update('', parent, attempt)
        commit = run('commit-tree', written, *(['-p', parent] if parent else []), '-m', message)
        try:
            if push:
                run('push', '--quiet', remote, f'{commit}:{ref}')
            else:
                run('update-ref', ref, commit, tip or '0' * len(commit))
        except RefError as error:
            if attempt + 1 == ATTEMPTS:
                raise
            raced = any(sign in str(error) for sign in RACE_SIGNS)
            if raced:
                sleep(0.5 * 2 ** attempt + random.uniform(0, 0.25))
            moved = fetch() if push else _tip(ref, run)
            if (tip and not moved) or (moved == tip and not raced):
                raise
            tip = moved
        else:
            if push:
                run('update-ref', tracking_ref(remote, ref), commit)
            return Update(commit, parent, attempt)
    raise RefError('could not update the publication ref')


def _tip(ref: str, run: Callable) -> str:
    try:
        return run('rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}')
    except RefError:
        return ''
