"""Run a disposable site with two artifact versions for manual UI checks."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from projector import artifact, cli


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--base', default='/demo/')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='projector-artifact-preview-') as temp:
        root = Path(temp)
        subprocess.run(['git', 'init', '-q', '-b', 'trunk', str(root)], check=True)
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.com'), ('commit.gpgsign', 'false')]:
            subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
        source = root / 'source'
        artifact.init('cache-explorer', source, 'Cache explorer', 'Explore versioned cache data')
        (source / 'index.html').write_bytes((Path(__file__).parent / 'fixtures/artifact/index.html').read_bytes())
        (source / 'bytes.bin').write_bytes(b'\x00\xff\x00\r\n')
        meta = json.loads((source / 'artifact.json').read_text())
        meta['files'] += ['cases.json', 'bytes.bin']
        (source / 'artifact.json').write_text(json.dumps(meta))
        for value in (17, 23):
            (source / 'cases.json').write_text(json.dumps({'value': value}))
            artifact.publish(artifact.snapshot(source), root, 'origin', push=False)
        print('Latest uses 23 and the older version uses 17; add ?value=6#details to check deep-link state.', flush=True)
        return cli.main(['site', 'serve', '--repo-root', str(root), '--no-fetch', '--no-prepare',
                         '--port', str(args.port), '--base', args.base])


if __name__ == '__main__':
    raise SystemExit(main())
