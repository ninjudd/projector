from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from projector import artifact, refs, site


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init', '-q', '-b', 'trunk')
        git(self.repo, 'config', 'user.name', 'Test')
        git(self.repo, 'config', 'user.email', 'test@example.com')
        git(self.repo, 'config', 'commit.gpgsign', 'false')
        (self.repo / 'README.md').write_text('tracked\n')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-qm', 'Start')
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True)
        git(self.repo, 'remote', 'add', 'origin', str(self.remote))
        self.source = self.root / 'bundle'
        artifact.init('explorer', self.source, 'Cache explorer', 'Explore the cache')
        self.manifest = json.loads((self.source / 'artifact.json').read_text())
        self.manifest['files'] += ['cases.json', 'bytes.bin']
        (self.source / 'cases.json').write_text('{"value":17}\n')
        (self.source / 'bytes.bin').write_bytes(b'\x00\xff\x00\r\n')
        self.write_manifest()

    def write_manifest(self):
        (self.source / 'artifact.json').write_text(json.dumps(self.manifest))

    def publish(self, push=False, root=None):
        return artifact.publish(artifact.snapshot(self.source), root or self.repo, 'origin', push=push)

    def test_versions_recover_exact_bytes_and_republishing_old_content_keeps_latest(self):
        first = self.publish()
        (self.source / 'cases.json').write_text('{"value":23}\n')
        second = self.publish()
        (self.source / 'cases.json').write_text('{"value":17}\n')
        tip = git(self.repo, 'rev-parse', artifact.REF)
        self.assertEqual(first.version, self.publish().version)
        self.assertEqual(tip, git(self.repo, 'rev-parse', artifact.REF))
        saved = artifact.read_refs(self.repo, 'origin')['explorer']
        self.assertEqual((second.version, 2), (saved.latest, len(saved.versions)))
        restored = self.root / 'restored'
        artifact.recover(saved, restored, first.version)
        self.assertEqual(b'{"value":17}\n', (restored / 'cases.json').read_bytes())
        self.assertEqual(b'\x00\xff\x00\r\n', (restored / 'bytes.bin').read_bytes())
        self.assertEqual(first.version, artifact.snapshot(restored).version)

    def test_metadata_changes_identity_but_file_order_does_not(self):
        first = artifact.snapshot(self.source).version
        self.manifest['files'].reverse()
        self.write_manifest()
        self.assertEqual(first, artifact.snapshot(self.source).version)
        self.manifest['description'] = 'Different question'
        self.write_manifest()
        self.assertNotEqual(first, artifact.snapshot(self.source).version)

    def test_unsafe_paths_missing_files_and_case_collisions_never_write_a_ref(self):
        (self.root / 'secret').write_text('outside the bundle')
        for name in ['.env', 'a\\b', 'a\nfile', 'CASES.json']:
            (self.source / name).write_text('must not publish')
        for paths in [['../secret'], [str(self.root / 'secret')], ['.env'], ['a\\b'], ['a\nfile'], ['missing'],
                      ['cases.json', 'CASES.json']]:
            with self.subTest(paths=paths):
                self.manifest['files'] = ['index.html', *paths]
                self.write_manifest()
                with self.assertRaises(artifact.ArtifactError):
                    self.publish()
                self.assertEqual('', refs.ref_tip(self.repo, artifact.REF))
        self.manifest['files'] = ['index.html', 'linked']
        (self.source / 'linked').symlink_to(self.source / 'index.html')
        self.write_manifest()
        with self.assertRaises(artifact.ArtifactError):
            self.publish()

    def test_size_limits_leave_source_untouched(self):
        before = (self.source / 'cases.json').read_bytes()
        with mock.patch.object(artifact, 'MAX_FILE', 4), self.assertRaises(artifact.ArtifactError):
            artifact.snapshot(self.source)
        self.assertEqual(before, (self.source / 'cases.json').read_bytes())

    def test_an_unknown_schema_cannot_be_published(self):
        self.manifest['schema'] = 2
        self.write_manifest()
        with self.assertRaises(artifact.ArtifactError):
            self.publish()
        self.assertEqual('', refs.ref_tip(self.repo, artifact.REF))

    def test_recovery_refuses_an_occupied_directory_without_partial_writes(self):
        self.publish()
        saved = artifact.read_refs(self.repo, 'origin')['explorer']
        out = self.root / 'occupied'
        out.mkdir()
        (out / 'keep').write_text('dirty')
        with self.assertRaises(artifact.ArtifactError):
            artifact.recover(saved, out)
        self.assertEqual(['keep'], [p.name for p in out.iterdir()])

    def test_racing_remote_publishes_preserve_both_versions_and_dirty_checkout(self):
        other = self.root / 'other'
        git(self.root, 'clone', '-q', str(self.repo), str(other))
        git(other, 'remote', 'set-url', 'origin', str(self.remote))
        git(other, 'config', 'user.name', 'Other')
        git(other, 'config', 'user.email', 'other@example.com')
        git(other, 'config', 'commit.gpgsign', 'false')
        theirs = artifact.snapshot(self.source)
        (self.source / 'cases.json').write_text('{"value":29}\n')
        mine = artifact.snapshot(self.source)
        (self.repo / 'README.md').write_text('dirty\n')
        branch = git(self.repo, 'rev-parse', 'HEAD')
        index = (self.repo / '.git/index').read_bytes()
        real = refs.git
        raced = []

        def racing(*args, **kwargs):
            if args[0] == 'push' and not raced:
                raced.append(True)
                artifact.publish(theirs, other, 'origin', push=True)
            return real(*args, **kwargs)

        with mock.patch.object(refs, 'git', side_effect=racing), mock.patch.object(refs.time, 'sleep'):
            artifact.publish(mine, self.repo, 'origin', push=True)
        saved = artifact.read_refs(self.repo, 'origin')['explorer']
        self.assertEqual((mine.version, {mine.version, theirs.version}), (saved.latest, set(saved.versions)))
        self.assertEqual(branch, git(self.repo, 'rev-parse', 'HEAD'))
        self.assertEqual(index, (self.repo / '.git/index').read_bytes())
        self.assertEqual('dirty\n', (self.repo / 'README.md').read_text())

    def test_site_keeps_fixed_versions_and_searches_only_latest_visible_text(self):
        (self.source / 'index.html').write_text('<title>Old</title><p>Obsolete text</p>')
        first = self.publish()
        (self.source / 'index.html').write_text('<title>New</title><p>Current text</p><script>Hidden text</script>')
        second = self.publish()
        out = self.root / 'site'
        site.build_site(out, artifacts=artifact.read_refs(self.repo, 'origin'), base='/demo/')
        manifest = json.loads((out / 'site.json').read_text())
        self.assertEqual(second.version, manifest['artifacts'][0]['latest'])
        for version in [first.version, second.version]:
            self.assertTrue((out / f'artifacts/explorer/{version}/index.html').is_file())
            self.assertEqual(b'\x00\xff\x00\r\n',
                             (out / f'content/artifacts/explorer/{version}/bytes.bin').read_bytes())
        search = json.loads((out / 'search.json').read_text())
        self.assertEqual(1, len(search))
        self.assertIn('Current text', search[0]['text'])
        self.assertNotIn('Obsolete text', search[0]['text'])
        self.assertNotIn('Hidden text', search[0]['text'])
        self.assertEqual('artifacts/explorer/', search[0]['route'])

    def test_a_corrupt_latest_version_keeps_valid_fixed_versions_but_has_no_directory_entry(self):
        first = self.publish()
        (self.source / 'cases.json').write_text('{"value":31}\n')
        second = self.publish()
        parent = refs.ref_tip(self.repo, artifact.REF)
        run = lambda *args, **kwargs: refs.git(*args, root=self.repo, **kwargs)
        tree = refs.write_tree(parent, {f'artifacts/explorer/{second.version}/files/cases.json': b'corrupt'}, run=run)
        commit = run('commit-tree', tree, '-p', parent, '-m', 'Corrupt a fixture')
        run('update-ref', artifact.REF, commit)
        warnings = []
        saved = artifact.read_refs(self.repo, 'origin', report=warnings.append)
        self.assertEqual((None, {first.version}), (saved['explorer'].latest, set(saved['explorer'].versions)))
        self.assertIn(second.version, '\n'.join(warnings))
        out = self.root / 'corrupt-site'
        site.build_site(out, artifacts=saved)
        self.assertTrue((out / f'artifacts/explorer/{first.version}/index.html').exists())
        self.assertFalse((out / 'artifacts/explorer/index.html').exists())
        self.assertEqual([], json.loads((out / 'search.json').read_text()))

    def test_local_and_remote_versions_stay_separate_and_local_preview_wins(self):
        remote = self.publish(push=True)
        (self.source / 'cases.json').write_text('{"value":41}\n')
        local = self.publish()
        saved = artifact.read_refs(self.repo, 'origin')['explorer']
        self.assertEqual((local.version, True, {remote.version, local.version}),
                         (saved.latest, saved.preview, set(saved.versions)))
        remote_tree = artifact.GitReader(self.remote, artifact.REF)
        self.assertNotIn(f'explorer/{local.version}/artifact.json', remote_tree.entries)

    def test_a_running_server_watches_local_and_fetched_artifacts_and_keeps_reviews_offline(self):
        from tests.test_site import PLAN_DIFF, served_summary
        from projector import summary

        run = lambda *args, **kwargs: refs.git(*args, root=self.repo, **kwargs)
        folder = f'summaries/9/{"c" * 40}'
        tree = refs.write_tree('', {f'{folder}/summary.json': json.dumps(served_summary(9, 'c' * 40)),
                                    f'{folder}/diff.patch': PLAN_DIFF}, run=run)
        run('update-ref', summary.PAGES_REF, run('commit-tree', tree, '-m', 'Save a review fixture'))
        code = '''
from projector import cli, summary
from projector.site import serve
def offline(*args, **kwargs):
    raise summary.SummaryError('offline fixture')
summary.gh = offline
summary.gh_lookup = lambda *args, **kwargs: None
serve.FETCH_INTERVAL = 0.05
raise SystemExit(cli.main())
'''
        env = {**os.environ, 'PYTHONPATH': str(Path(artifact.__file__).resolve().parents[1])}
        process = subprocess.Popen([sys.executable, '-u', '-c', code, 'site', 'serve', '--repo-root', str(self.repo),
                                    '--port', '0', '--no-prepare'], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, env=env)
        lines = queue.Queue()

        def read_lines():
            for line in process.stdout:
                lines.put(line)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        output = []
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    self.fail('the preview did not start: ' + ''.join(output))
                output.append(line)
                if line.startswith('serving http://'):
                    url = line.split(';')[0].split(' ', 1)[1] + 'site.json'
                    break
            else:
                self.fail('the preview did not start: ' + ''.join(output))

            def until(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        manifest = json.load(response)
                    if predicate(manifest):
                        return manifest
                    time.sleep(0.05)
                self.fail(f'the running preview did not update: {manifest}')

            until(lambda data: len(data['reviews']) == 1 and not data['artifacts'])
            local = self.publish()
            until(lambda data: any(item['latest'] == local.version and item['preview'] for item in data['artifacts']))
            other = self.root / 'other'
            git(self.root, 'clone', '-q', str(self.repo), str(other))
            git(other, 'remote', 'set-url', 'origin', str(self.remote))
            for key, value in [('user.name', 'Other'), ('user.email', 'other@example.com'), ('commit.gpgsign', 'false')]:
                git(other, 'config', key, value)
            source = self.root / 'remote-bundle'
            artifact.init('remote-explorer', source, 'Remote explorer')
            remote = artifact.publish(artifact.snapshot(source), other, 'origin', push=True)
            until(lambda data: {item['latest'] for item in data['artifacts']} == {local.version, remote.version})
            git(self.repo, 'remote', 'set-url', 'origin', str(self.root / 'missing.git'))
            (self.source / 'cases.json').write_text('{"value":47}\n')
            changed = self.publish()
            manifest = until(lambda data: {item['latest'] for item in data['artifacts']} == {changed.version, remote.version})
            self.assertEqual([9], [row['number'] for row in manifest['reviews']])
            self.assertIsNone(process.poll(), 'the same server stays running through local, remote, and offline updates')
        finally:
            process.terminate()
            process.wait(timeout=5)
            reader.join(timeout=1)
            process.stdout.close()

    def test_a_local_compare_and_swap_race_keeps_both_publishers(self):
        theirs = artifact.snapshot(self.source)
        (self.source / 'cases.json').write_text('{"value":43}\n')
        mine = artifact.snapshot(self.source)
        real = refs.git
        raced = []

        def racing(*args, **kwargs):
            if args[:2] == ('update-ref', artifact.REF) and not raced:
                raced.append(True)
                artifact.publish(theirs, self.repo, 'origin', push=False)
            return real(*args, **kwargs)

        with mock.patch.object(refs, 'git', side_effect=racing), mock.patch.object(refs.time, 'sleep'):
            artifact.publish(mine, self.repo, 'origin', push=False)
        saved = artifact.read_refs(self.repo, 'origin')['explorer']
        self.assertEqual((mine.version, {mine.version, theirs.version}), (saved.latest, set(saved.versions)))

    def test_the_cli_keeps_unhosted_publication_local_and_recovers_its_source(self):
        import contextlib
        import io
        from projector import cli
        output = io.StringIO()
        args = ['--checkout', str(self.repo)]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.assertEqual(0, cli.main(['artifact', 'publish', '--dir', str(self.source), *args]))
            self.assertEqual(0, cli.main(['artifact', 'list', '--id', 'explorer', '--no-fetch', '--json', *args]))
            recovered = self.root / 'cli-recovered'
            self.assertEqual(0, cli.main(['artifact', 'init', 'explorer', '--from-published', '--no-fetch',
                                         '--dir', str(recovered), *args]))
        self.assertIn('preview with project site serve', output.getvalue())
        self.assertEqual(artifact.snapshot(self.source).version, artifact.snapshot(recovered).version)
        self.assertEqual('', refs.ref_tip(self.remote, artifact.REF))

    def test_a_dispatch_failure_reports_saved_content_and_a_workflow_retry(self):
        import contextlib
        import io
        from projector import cli, summary
        output = io.StringIO()
        with mock.patch.object(artifact, 'repository', return_value='owner/repo'), \
             mock.patch.object(summary, 'push_decision', return_value=('https://example.test/demo/', '')), \
             mock.patch.object(summary, 'default_branch', return_value='trunk'), \
             mock.patch.object(summary, 'site_workflow', return_value=summary.WORKFLOW_PATH), \
             mock.patch.object(summary, 'gh', side_effect=summary.SummaryError('permission denied')), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cli.main(['artifact', 'publish', '--dir', str(self.source), '--checkout', str(self.repo)])
        self.assertEqual(65, status)
        self.assertTrue(refs.ref_tip(self.remote, artifact.REF))
        self.assertIn('content is saved, but deployment did not start', output.getvalue())
        self.assertIn('gh workflow run projector-site.yml --repo owner/repo --ref trunk', output.getvalue())

    def test_unknown_visibility_cannot_authorize_public_hosting(self):
        from projector import summary
        with mock.patch.object(summary, 'gh_lookup', return_value=None), \
             self.assertRaisesRegex(summary.SummaryError, 'could not confirm'):
            summary.exposure('owner/repo', {'public': True})

    def test_a_checkout_without_origin_can_save_an_artifact_locally(self):
        import contextlib
        import io
        from projector import cli
        git(self.repo, 'remote', 'remove', 'origin')
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(artifact.summary, 'push_decision') as hosting:
            code = cli.main(['artifact', 'publish', '--dir', str(self.source), '--checkout', str(self.repo)])
        self.assertEqual(0, code)
        self.assertTrue(refs.ref_tip(self.repo, artifact.REF))
        hosting.assert_not_called()

    def test_dispatch_uses_the_existing_workflow_and_remote_default_branch(self):
        import contextlib
        import io
        from projector import cli, summary
        out = io.StringIO()
        with mock.patch.object(artifact, 'repository', return_value='owner/repo'), \
             mock.patch.object(summary, 'push_decision', return_value=('https://example.test/demo/', '')), \
             mock.patch.object(summary, 'site_workflow', return_value='.github/workflows/walkthroughs.yml'), \
             mock.patch.object(summary, 'gh', side_effect=['trunk\n', '']) as gh, \
             contextlib.redirect_stdout(out):
            code = cli.main(['artifact', 'publish', '--dir', str(self.source), '--checkout', str(self.repo)])
        self.assertEqual(0, code)
        self.assertEqual(('api', 'repos/owner/repo/actions/workflows/walkthroughs.yml/dispatches', '-f', 'ref=trunk'),
                         gh.call_args.args)
        self.assertIn('latest: https://example.test/demo/artifacts/explorer/', out.getvalue())
        self.assertTrue(refs.ref_tip(self.remote, artifact.REF))
