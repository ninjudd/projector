"""Measure artifact publication and builds without network access or retained files."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import time
from pathlib import Path
from projector import artifact, site


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="projector-artifact-cost-") as temp:
        root = Path(temp)
        subprocess.run(['git', 'init', '-q', '-b', 'trunk', str(root)], check=True)
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.com'), ('commit.gpgsign', 'false')]:
            subprocess.run(['git', '-C', str(root), 'config', key, value], check=True)
        source = root / 'source'
        artifact.init('sample', source, 'Sample')
        meta = json.loads((source / 'artifact.json').read_text())
        meta['files'] += ['cases.json']
        read_bytes = 0
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(100):
                meta['id'] = f'example-{i}'
                (source / 'artifact.json').write_text(json.dumps(meta))
                for version in range(5):
                    (source / 'cases.json').write_text(json.dumps({'value': version}))
                    bundle = artifact.snapshot(source)
                    read_bytes += sum(len(b) for b in bundle.files.values())
                    artifact.publish(bundle, root, 'origin', push=False)
            history_publish = time.perf_counter() - start
            meta['id'] = 'near-limit'
            meta['files'] = ['index.html', 'first.bin', 'second.bin']
            (source / 'artifact.json').write_text(json.dumps(meta))
            (source / 'first.bin').write_bytes(b'a' * artifact.MAX_FILE)
            (source / 'second.bin').write_bytes(b'b' * (artifact.MAX_FILE - 1024))
            start = time.perf_counter()
            bundle = artifact.snapshot(source)
            near_bytes = sum(len(b) for b in bundle.files.values())
            artifact.publish(bundle, root, 'origin', push=False)
            near_publish = time.perf_counter() - start
            start = time.perf_counter()
            catalog = artifact.read_refs(root, 'origin')
            site.build_site(root / 'site', artifacts=catalog)
            build = time.perf_counter() - start
        output = sum(p.stat().st_size for p in (root / 'site').rglob('*') if p.is_file())
        print(json.dumps({'small_artifacts':100, 'versions_each':5, 'small_source_bytes':read_bytes,
                          'history_publish_seconds':round(history_publish, 3), 'near_limit_bytes':near_bytes,
                          'near_limit_publish_seconds':round(near_publish, 3), 'read_and_build_seconds':round(build, 3),
                          'site_output_bytes':output}, indent=2))


if __name__ == "__main__":
    main()
