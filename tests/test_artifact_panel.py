from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from projector import artifact, artifact_panel, cli, console, refs


class ArtifactPanelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        self.env = mock.patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.source = self.root / 'source'
        artifact.init('explorer', self.source, 'Cache explorer', 'Explore saved data')
        self.save()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def save(self):
        return artifact.publish(artifact.snapshot(self.source), self.repo, 'origin', push=False)

    def listed(self, session='one'):
        return artifact_panel.listing(self.repo, 'origin', session)

    def test_existing_artifacts_appear_and_dismissal_survives_a_revision_only_in_its_session(self):
        first = self.listed()[0]
        tip = refs.ref_tip(self.repo, artifact.REF)
        artifact_panel.dismiss(self.repo, 'origin', 'one', 'explorer')
        self.assertEqual([], self.listed())
        self.assertEqual(tip, refs.ref_tip(self.repo, artifact.REF))
        self.assertEqual('Cache explorer', self.listed('two')[0]['title'])
        meta = json.loads((self.source / 'artifact.json').read_text())
        meta['title'] = 'Revised explorer'
        (self.source / 'artifact.json').write_text(json.dumps(meta))
        self.save()
        self.assertEqual([], self.listed())
        self.assertEqual('Revised explorer', self.listed('two')[0]['title'])
        artifact_panel.dismiss(self.repo, 'origin', 'one', 'explorer', restore=True)
        self.assertEqual(first['key'], self.listed()[0]['key'])
        self.assertEqual('Revised explorer', self.listed()[0]['title'])

    def test_cached_metadata_does_not_load_payloads_and_invalid_latest_keeps_valid_neighbors(self):
        read = artifact.GitReader.read
        paths = []

        def recorded(reader, path, limit):
            paths.append(path)
            self.assertNotIn('/files/', path)
            return read(reader, path, limit)

        with mock.patch.object(artifact.GitReader, 'read', recorded):
            self.assertEqual('explorer', self.listed()[0]['id'])
        with mock.patch.object(artifact, 'GitReader', side_effect=AssertionError('unchanged refs were rescanned')):
            self.assertEqual('explorer', self.listed()[0]['id'])
        parent = refs.ref_tip(self.repo, artifact.REF)
        tree = refs.write_tree(parent, {'artifacts/broken/latest.json': b'{"version":"bad"}'},
                               run=lambda *args, **kwargs: refs.git(*args, root=self.repo, **kwargs))
        commit = self.git('commit-tree', tree, '-p', parent, '-m', 'Add invalid neighbor')
        self.git('update-ref', artifact.REF, commit)
        self.assertEqual(['explorer'], [row['id'] for row in self.listed()])
        self.assertTrue(paths)

    def test_local_open_serves_relative_binary_files_reuses_the_preview_and_checks_the_host(self):
        meta = json.loads((self.source / 'artifact.json').read_text())
        meta['files'].append('data.bin')
        (self.source / 'artifact.json').write_text(json.dumps(meta))
        (self.source / 'data.bin').write_bytes(b'\x00\xff\x00\r\n')
        self.save()
        opened = subprocess.run([sys.executable, '-m', 'projector', 'artifact', 'open', 'explorer',
                                 '--checkout', str(self.repo), '--print-url'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, opened.returncode, opened.stderr)
        url = opened.stdout.strip()
        registry = next((artifact_panel.watch.state_root() / 'artifact-previews').glob('*/preview.json'))
        pid = json.loads(registry.read_text())['pid']
        self.addCleanup(lambda: os.kill(pid, signal.SIGTERM))
        with urllib.request.urlopen(url) as response:
            self.assertIn(b'Cache explorer', response.read())
        with urllib.request.urlopen(url.removesuffix('index.html') + 'data.bin') as response:
            self.assertEqual(b'\x00\xff\x00\r\n', response.read())
        self.assertEqual(url, artifact_panel.open_url(self.repo, 'origin', 'explorer'))
        for method in ('GET', 'HEAD'):
            request = urllib.request.Request(url, method=method, headers={'Host': 'untrusted.example'})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(404, error.exception.code)

    def test_console_opens_and_dismisses_an_artifact_without_changing_its_saved_versions(self):
        state = artifact_panel.watch.state_root()
        app = console.Console(state)
        app.sessions['one'] = console.Session('one', 'codex', 'One', artifacts=self.listed())
        previous = list(app.sessions['one'].artifacts)
        app.open_panel('one')
        app.key('enter')
        self.assertIn('Artifacts', '\n'.join(line.text for line in app.panel_lines(100)[0]))
        with mock.patch.object(artifact_panel, 'open_url', return_value='https://example.com/artifacts/explorer/'):
            with mock.patch.object(app, 'open_url') as opened:
                app.key('o')
                opened.assert_called_once_with('https://example.com/artifacts/explorer/')
        tip = refs.ref_tip(self.repo, artifact.REF)
        app.key('8')
        self.assertEqual([], self.listed())
        self.assertEqual([], app.sessions['one'].artifacts)
        self.assertEqual(tip, refs.ref_tip(self.repo, artifact.REF))
        app.apply(console.Found(artifacts={'one': previous}))
        self.assertEqual([], app.sessions['one'].artifacts)

    def test_an_idle_preview_stops_and_removes_its_extracted_files(self):
        work = self.root / 'preview'
        artifact.write_source(artifact.snapshot(self.source), work / 'files')
        result = subprocess.run([sys.executable, '-c',
                                 'from pathlib import Path; import sys; '
                                 'from projector.artifact_panel import serve; '
                                 'serve(Path(sys.argv[1]), "test-token", idle=0.01)', str(work)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(work.exists())

    def test_a_fetched_published_artifact_opens_its_configured_site_without_a_preview_process(self):
        tip = refs.ref_tip(self.repo, artifact.REF)
        self.git('update-ref', refs.tracking_ref('origin', artifact.REF), tip)
        self.git('update-ref', '-d', artifact.REF)
        self.assertFalse(self.listed()[0]['preview'])
        with mock.patch.object(artifact, 'repository', return_value='example/repo'):
            with mock.patch.object(artifact_panel.summary, 'push_decision', return_value=('https://example.com/tools/', '')):
                self.assertEqual('https://example.com/tools/artifacts/explorer/',
                                 artifact_panel.open_url(self.repo, 'origin', 'explorer'))
        self.assertFalse((artifact_panel.watch.state_root() / 'artifact-previews').exists())

    def test_cli_dismiss_and_restore_change_only_the_selected_session(self):
        from contextlib import redirect_stdout
        import io
        arguments = ['--checkout', str(self.repo), '--session', 'one']
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(['artifact', 'dismiss', 'explorer', *arguments]))
            self.assertEqual([], self.listed())
            self.assertEqual(1, len(self.listed('two')))
            self.assertEqual(0, cli.main(['artifact', 'restore', 'explorer', *arguments]))
        self.assertEqual(1, len(self.listed()))

    def test_a_stopped_managed_session_still_lists_and_opens_its_artifacts(self):
        from projector import agent_console
        state = artifact_panel.watch.state_root()
        app = agent_console.Console(state)
        managed = {'one': {'cwd': str(self.repo), 'backend': 'codex', 'state': 'stopped'}}
        with mock.patch.object(console, 'live_sessions', return_value={}):
            with mock.patch.object(agent_console.runtime, 'sessions', return_value=managed):
                with mock.patch.object(agent_console.runtime, 'launches', return_value=[]):
                    found = app.load(console.Work(inbox=True, rows=True, signatures={}))
        app.apply(found)
        self.assertEqual('explorer', app.sessions['one'].artifacts[0]['id'])
        app.open_panel('one')
        app.key('enter')
        with mock.patch.object(artifact_panel, 'open_url', return_value='https://example.com/artifacts/explorer/'):
            with mock.patch.object(app, 'open_url') as opened:
                with mock.patch.object(agent_console.runtime, 'control') as control:
                    app.key('o')
                    opened.assert_called_once_with('https://example.com/artifacts/explorer/')
                    control.assert_not_called()
